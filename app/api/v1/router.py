from fastapi import APIRouter

from app.api.v1 import auth, equipment, files, onedrive, projects, sync, users, versions

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(onedrive.router, tags=["onedrive"])
api_router.include_router(files.router, tags=["files"])
api_router.include_router(equipment.router, tags=["equipment"])
api_router.include_router(versions.router, tags=["versions"])
api_router.include_router(sync.router, tags=["sync"])
