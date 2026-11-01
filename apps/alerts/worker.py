"""
The alert worker, run inside `manage.py run_monitor` next to the checks (PLAN.md §7.2):

- deliver loop: every few seconds queue what's due (outbox.sweep) and send pending alerts;
- poll loop: long-poll Telegram for "/start <token>" (linking) and "/status".

Both are separate asyncio tasks, so a slow or unreachable Telegram never delays the checks. When Telegram
can't be reached, alerts stay queued and the loop backs off (up to 5 minutes) before trying again.
"""

import asyncio
import logging

from asgiref.sync import sync_to_async
from django.db import close_old_connections

from . import outbox, telegram
from .models import AlertSettings

log = logging.getLogger("netcore.alerts")

TICK_SECONDS = 2
MAX_BACKOFF = 300


def _active_token():
    alert_settings = AlertSettings.load()
    return alert_settings.bot_token if alert_settings.active else ""


# Each round is split like the check engine: database steps (sync) and network steps (async, no database).


def prepare():
    """Queue what's due; return (token, [(notification, chat id, text)]) to send."""
    outbox.sweep()
    token = _active_token()
    return token, outbox.due() if token else []


async def send_batch(token, batch):
    """Send each alert. Returns [(notification, error or None)]; stops at the first network error."""
    results = []
    for notification, chat_id, text in batch:
        try:
            await telegram.asend(token, chat_id, text)
        except telegram.TelegramError as e:
            results.append((notification, e))
            if e.network:
                break  # the rest stay queued; try again after a pause
            continue
        results.append((notification, None))
    return results


def record(results):
    """Store the outcome. Returns False if Telegram couldn't be reached."""
    for notification, error in results:
        if error is None:
            outbox.mark_sent(notification)
        else:
            outbox.mark_failed(notification, error)
    return not any(error is not None and error.network for _n, error in results)


async def deliver_round():
    token, batch = await sync_to_async(prepare)()
    results = await send_batch(token, batch)
    return await sync_to_async(record)(results)


async def _sleep(stop, seconds):
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def deliver_loop(stop, tick=TICK_SECONDS):
    backoff = 0
    while not stop.is_set():
        try:
            reached = await deliver_round()
        except Exception:  # noqa: BLE001 — log and keep alerting
            log.exception("Alert delivery error")
            await sync_to_async(close_old_connections)()
            reached = False
        if reached:
            if backoff:
                log.info("Telegram reachable again; sending queued alerts")
            backoff = 0
        else:
            backoff = min(max(backoff * 2, 10), MAX_BACKOFF)
            log.warning("Telegram unreachable; alerts stay queued, retrying in %ss", backoff)
        await _sleep(stop, backoff or tick)


def poll_state():
    """(token, next update offset); no token means there's no bot to poll."""
    return _active_token(), AlertSettings.load().last_update_id + 1


def handle_updates(updates):
    """Answer each message (linking, /status) and move the offset on. Returns [(chat id, reply)]."""
    replies = []
    for update in updates:
        reply = outbox.handle_update(update)
        AlertSettings.objects.filter(pk=1, last_update_id__lt=update["update_id"]).update(
            last_update_id=update["update_id"])
        if reply:
            replies.append(reply)
    return replies


async def send_replies(token, replies):
    for chat_id, text in replies:
        try:
            await telegram.asend(token, chat_id, text)
        except telegram.TelegramError as e:
            log.warning("Couldn't answer a Telegram message: %s", e)


async def poll_round(wait=20):
    """Fetch and answer one batch of messages. Returns False if there's no bot to poll."""
    token, offset = await sync_to_async(poll_state)()
    if not token:
        return False
    updates = await telegram.get_updates(token, offset, wait=wait)
    await send_replies(token, await sync_to_async(handle_updates)(updates))
    return True


async def poll_loop(stop, wait=20):
    backoff = 0
    while not stop.is_set():
        try:
            active = await poll_round(wait)
            backoff = 0
            if not active:
                await _sleep(stop, 10)  # no bot set up yet: check the settings again shortly
        except telegram.TelegramError as e:
            backoff = min(max(backoff * 2, 10), MAX_BACKOFF)
            log.warning("Telegram polling failed (%s); retrying in %ss", e, backoff)
            await _sleep(stop, backoff)
        except Exception:  # noqa: BLE001
            log.exception("Telegram polling error")
            await sync_to_async(close_old_connections)()
            await _sleep(stop, 10)


async def run(stop):
    """Both loops until `stop` is set. A long-poll in progress is cancelled rather than waited for."""
    poller = asyncio.create_task(poll_loop(stop))
    try:
        await deliver_loop(stop)
    finally:
        poller.cancel()
        try:
            await poller
        except asyncio.CancelledError:
            pass
