import asyncio
import threading
from app.workers.monitor import main
from app.scrap_news.scraper import main as scraper_main

if __name__ == "__main__":
    try:
        # Run the scraper in a background thread (it's synchronous/blocking)
        scraper_thread = threading.Thread(target=scraper_main, name="scraper", daemon=True)
        scraper_thread.start()

        # Run the async monitor on the main thread
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nWorker shutting down...")
