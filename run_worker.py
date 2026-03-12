import asyncio
import threading
from app.workers.monitor import main
# from app.scrap_news.scraper import main as scraper_main

if __name__ == "__main__":
    try:
        # Run the async monitor on the main thread
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nWorker shutting down...")
