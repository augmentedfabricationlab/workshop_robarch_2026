"""Small background-job helper for Grasshopper Python components.

Network calls run outside Grasshopper's solution thread.  The result stays in
``scriptcontext.sticky`` until the input signature changes, the user starts a
new run, or reset is pressed.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


def _sticky():
    import scriptcontext as sc

    return sc.sticky


def _key(component: Any, name: str) -> str:
    guid = str(getattr(component, "InstanceGuid", "component"))
    return "robarch.jobs.{}.{}".format(guid, name)


def _rising_edge(store: dict, key: str, trigger: bool) -> bool:
    edge_key = key + ".trigger"
    previous = bool(store.get(edge_key, False))
    current = bool(trigger)
    store[edge_key] = current
    return current and not previous


def _schedule(component: Any, delay: int = 400) -> None:
    """Ask Grasshopper to poll a running job again."""
    try:
        import Grasshopper

        document = component.OnPingDocument()
        if document:
            callback = Grasshopper.Kernel.GH_Document.GH_ScheduleDelegate(
                lambda _: component.ExpireSolution(False)
            )
            document.ScheduleSolution(int(delay), callback)
    except Exception:
        pass


def background_step(
    component: Any,
    name: str,
    signature: str,
    work: Callable[[], Any],
    trigger: bool = False,
    reset: bool = False,
) -> dict:
    """Start or poll one job and return its serialisable state.

    A Grasshopper Button only stays true for one solution.  Therefore a false
    ``trigger`` still polls an already-running job.  Pressing the Button again
    after completion deliberately starts a fresh call with the same inputs.
    """
    store = _sticky()
    key = _key(component, name)
    rising = _rising_edge(store, key, trigger)
    if reset:
        store.pop(key, None)
        return {"status": "idle"}

    job = store.get(key)
    should_start = rising and (
        not job or job.get("signature") != signature or job.get("done")
    )
    if should_start:
        job = {
            "signature": signature,
            "status": "running",
            "done": False,
            "result": None,
            "error": None,
            "started": time.time(),
        }
        store[key] = job

        def run() -> None:
            try:
                job["result"] = work()
                job["status"] = "complete"
            except Exception as exc:
                job["error"] = "{}: {}".format(type(exc).__name__, exc)
                job["status"] = "error"
            finally:
                job["elapsedSeconds"] = round(time.time() - job["started"], 3)
                job["done"] = True

        threading.Thread(target=run, daemon=True).start()

    if not job:
        return {"status": "idle"}
    if job.get("signature") != signature:
        return {
            "status": "stale",
            "message": "inputs changed; press the Button to generate again",
        }
    if not job.get("done"):
        _schedule(component)
    return job


def clear_job(component: Any, name: str) -> None:
    """Remove a component's cached job."""
    store = _sticky()
    key = _key(component, name)
    store.pop(key, None)
    store.pop(key + ".trigger", None)


def update_progress(
    component: Any, name: str, completed: int, total: int, message: str = ""
) -> None:
    """Expose compact progress from work running in a background thread."""
    try:
        job = _sticky().get(_key(component, name))
        if job and not job.get("done"):
            job["progress"] = {
                "completed": int(completed),
                "total": int(total),
                "message": str(message or ""),
            }
    except Exception:
        pass


def cached_step(
    component: Any,
    name: str,
    signature: str,
    work: Callable[[], Any],
    trigger: bool = False,
) -> dict:
    """Run a local stage on a Button pulse and retain its last output.

    This is the synchronous counterpart to :func:`background_step`, useful for
    Rhino operations that must stay on Grasshopper's solution thread.
    """
    store = _sticky()
    key = _key(component, name)
    state = store.get(key)
    if _rising_edge(store, key, trigger):
        started = time.time()
        try:
            state = {
                "signature": signature,
                "status": "complete",
                "result": work(),
                "error": None,
                "elapsedSeconds": round(time.time() - started, 3),
            }
        except Exception as exc:
            state = {
                "signature": signature,
                "status": "error",
                "result": None,
                "error": "{}: {}".format(type(exc).__name__, exc),
                "elapsedSeconds": round(time.time() - started, 3),
            }
        store[key] = state
    if not state:
        return {"status": "idle"}
    if state.get("signature") != signature:
        return {
            "status": "stale",
            "message": "inputs changed; press the Button to run this stage again",
        }
    return state


def automatic_step(
    component: Any, name: str, signature: str, work: Callable[[], Any]
) -> dict:
    """Run a local stage once whenever its input signature changes."""
    store = _sticky()
    key = _key(component, name)
    state = store.get(key)
    if not state or state.get("signature") != signature:
        started = time.time()
        try:
            state = {
                "signature": signature, "status": "complete", "result": work(),
                "error": None, "elapsedSeconds": round(time.time() - started, 3),
            }
        except Exception as exc:
            state = {
                "signature": signature, "status": "error", "result": None,
                "error": "{}: {}".format(type(exc).__name__, exc),
                "elapsedSeconds": round(time.time() - started, 3),
            }
        store[key] = state
    return state
