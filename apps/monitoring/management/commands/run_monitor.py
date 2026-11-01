import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

from apps.monitoring.engine import run_forever


class Command(BaseCommand):
    help = "Run the monitoring checks continuously (keep this running alongside the web server)."

    def handle(self, *args, **opts):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per check would flood the log
        self.stdout.write(self.style.SUCCESS("NetCore monitor running — press Ctrl+C to stop."))

        async def main():
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            await run_forever(stop)

        asyncio.run(main())
