import asyncio
import threading
from app.scrap_news.indian_scraper import main
# from app.workers.monitor import main as global_main
# from app.scrap_news.scraper import main as scraper_main

if __name__ == "__main__":
    try:
        # Run the async Indian scraper on the main thread
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nWorker shutting down...")
