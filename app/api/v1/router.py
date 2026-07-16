from fastapi import APIRouter

from app.api.v1 import (
    auth,
    equipment,
    files,
    onedrive,
    pending_changes,
    projects,
    sync,
    users,
    versions,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(onedrive.router, tags=["onedrive"])
api_router.include_router(files.router, tags=["files"])
# Registered BEFORE equipment.router: equipment.router has
# GET /projects/{project_id}/equipment/{equipment_id} (equipment_id: int).
# Starlette matches routes by path SHAPE in registration order, not by
# type — a request to .../equipment/pending would otherwise match that
# route first (treating "pending" as equipment_id) and fail int coercion
# with a 422, before ever reaching this router's literal /equipment/pending
# path. More specific literal routes must come first.
api_router.include_router(pending_changes.router, tags=["pending-changes"])
api_router.include_router(equipment.router, tags=["equipment"])
api_router.include_router(versions.router, tags=["versions"])
api_router.include_router(sync.router, tags=["sync"])
