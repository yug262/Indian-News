import asyncio
from app.scrapers.indian_scraper import main

if __name__ == "__main__":
    try:
        # Run the async Indian scraper on the main thread
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nWorker shutting down...")
