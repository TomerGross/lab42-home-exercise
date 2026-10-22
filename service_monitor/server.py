"""HTTP layer: the FastAPI app, its endpoints and error responses. The only module that knows about HTTP."""

import os
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Body, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from service_monitor.database import Database
from service_monitor.errors import ServiceMonitorError
from service_monitor.schemas import Service, ServiceConfig
from service_monitor.service_checker import DEFAULT_TIMEOUT_SECONDS, TcpServiceChecker
from service_monitor.service_manager import ServiceManager

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./services.db"


def get_manager(request: Request) -> ServiceManager:
    return request.app.state.manager


Manager = Annotated[ServiceManager, Depends(get_manager)]
router = APIRouter()


# --- Routes ---


@router.post("/services", status_code=HTTPStatus.CREATED)
async def add_services(
    services: Annotated[list[ServiceConfig], Body(embed=True, min_length=1)], manager: Manager
) -> list[Service]:
    return await manager.add_services(services)


@router.get("/services")
async def list_services(manager: Manager) -> list[Service]:
    return await manager.list_services()


@router.get("/services/{name}")
async def get_service(name: str, manager: Manager) -> Service:
    return await manager.get_service(name)


@router.post("/services/check")
async def check_services(
    manager: Manager, names: Annotated[list[str] | None, Body(embed=True, min_length=1)] = None
) -> list[Service]:
    return await manager.check_services(names)


@router.post("/services/{name}/check")
async def check_service(name: str, manager: Manager) -> Service:
    return await manager.check_service(name)


@router.delete("/services/{name}", status_code=HTTPStatus.NO_CONTENT)
async def delete_service(name: str, manager: Manager) -> None:
    await manager.delete_service(name)


# --- Error handlers: every error becomes {"error": <code>, "detail": <message or list>} ---


async def handle_service_monitor_error(request: Request, error: ServiceMonitorError) -> JSONResponse:
    return error_response(error.status_code, error.code, str(error))


async def handle_invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    # loc looks like ("body", "services", 0, "port"); drop "body" to get "services.0.port".
    problems = [
        {"field": ".".join(str(part) for part in problem["loc"][1:]), "message": problem["msg"]}
        for problem in error.errors()
    ]
    return error_response(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_request", problems)


async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
    status_code = HTTPStatus(error.status_code)
    return error_response(status_code, status_code.phrase.lower().replace(" ", "_"), error.detail)


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    # The traceback is still logged by the server; the client never sees internal details.
    return error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "Internal server error")


def error_response(status_code: HTTPStatus, code: str, detail: str | list) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code, "detail": detail})


# --- App ---


def create_app(database_url: str | None = None, check_timeout_seconds: float | None = None) -> FastAPI:
    database_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    if check_timeout_seconds is None:
        check_timeout_seconds = float(os.getenv("CHECK_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(database_url)
        await database.create_tables()
        app.state.manager = ServiceManager(database, TcpServiceChecker(check_timeout_seconds))
        yield
        await database.close()

    app = FastAPI(
        title="Service Monitoring API",
        description="Manage a list of network services and check whether they are reachable over TCP.",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.add_exception_handler(ServiceMonitorError, handle_service_monitor_error)
    app.add_exception_handler(RequestValidationError, handle_invalid_request)
    app.add_exception_handler(HTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
    return app


app = create_app()
