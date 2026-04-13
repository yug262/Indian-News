import os
import asyncio
import json
import logging
import select
import psycopg2
from typing import Set, Dict
from app.core.db import DB_CONFIG, execute_notify

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("realtime")

class ConnectionManager:
    """
    Manages active SSE client connections using asyncio Queues.
    This enables a true push-based architecture where events are broadcast 
    to all connected tabs instantly.
    """
    def __init__(self):
        self.active_connections: Set[asyncio.Queue] = set()

    async def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue()
        self.active_connections.add(queue)
        logger.info(f"[REALTIME] New client subscribed. Total: {len(self.active_connections)}")
        return queue

    async def unsubscribe(self, queue: asyncio.Queue):
        if queue in self.active_connections:
            self.active_connections.remove(queue)
            logger.info(f"[REALTIME] Client unsubscribed. Total: {len(self.active_connections)}")

    async def broadcast(self, event_type: str, news_id: int):
        payload = {"type": event_type, "news_id": news_id}
        message = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        
        if not self.active_connections:
            return

        # Push to all active queues
        tasks = [queue.put(message) for queue in self.active_connections]
        await asyncio.gather(*tasks)

# Global connection manager instance
manager = ConnectionManager()

async def db_listener():
    """
    Resilient background task that maintains a dedicated connection to Postgres 
    and LISTENs for 'indian_news_events'. 
    It auto-reconnects on failure and broadcasts signals to all connected clients.
    """
    logger.info("[REALTIME] Starting background DB listener...")
    channel = "indian_news_events"
    
    while True:
        conn = None
        try:
            # Step 1: Establish dedicated connection (separate from pool)
            conn = psycopg2.connect(**DB_CONFIG)
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            
            with conn.cursor() as cur:
                cur.execute(f"LISTEN {channel};")
                logger.info(f"[REALTIME] Successfully listening to channel: {channel}")
                
                while True:
                    # Non-blocking check for notifications
                    if select.select([conn], [], [], 5) == ([], [], []):
                        # Timeout — yield back to the event loop so other tasks (like Uvicorn) can run
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        conn.poll()
                        while conn.notifies:
                            notify = conn.notifies.pop(0)
                            try:
                                payload = json.loads(notify.payload)
                                event_type = payload.get("type", "refresh")
                                news_id = payload.get("news_id")
                                
                                # Broadcast to all connected SSE browser tabs via asyncio
                                # We use call_soon_threadsafe if we were in a thread, 
                                # but the listener is an async task.
                                await manager.broadcast(event_type, news_id)
                            except Exception as e:
                                logger.error(f"[REALTIME] Failed to parse notification payload: {e}")

        except (psycopg2.OperationalError, psycopg2.InterfaceError) as db_err:
            logger.warning(f"[REALTIME] DB connection lost: {db_err}. Retrying in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"[REALTIME] Critical error in DB listener: {e}")
            await asyncio.sleep(5)
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass

# ---- Trigger Helpers (used by Scraper and API) ----

def trigger_indian_event(event_type: str, news_id: int):
    """Signals all devices via Postgres NOTIFY with a tiny payload."""
    payload = json.dumps({"type": event_type, "news_id": news_id})
    execute_notify("indian_news_events", payload)

def trigger_news_created(news_id: int):
    trigger_indian_event("news_created", news_id)

def trigger_analysis_completed(news_id: int):
    trigger_indian_event("analysis_completed", news_id)

def trigger_analysis_failed(news_id: int):
    trigger_indian_event("analysis_failed", news_id)
