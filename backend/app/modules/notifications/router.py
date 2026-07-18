from fastapi import APIRouter

# No public endpoints yet — this module currently reacts to events only
# (see app/tasks/notifications_tasks.py).
router = APIRouter(prefix="/notifications", tags=["notifications"])
