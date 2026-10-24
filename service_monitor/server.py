"""API layer: the FastAPI app, its routes and its error responses.

The only module that knows the web framework. Each route's docstring is its
description on the /docs page.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Body, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from service_monitor.config import Settings
from service_monitor.database import Database
from service_monitor.errors import ServiceMonitorError
from service_monitor.schemas import Service, ServiceConfig, ServiceStatus
from service_monitor.service_checker import NetworkServiceChecker
from service_monitor.service_manager import ServiceManager

DEFAULT_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 1000  # one request can never return an unbounded number of rows


def get_manager(request: Request) -> ServiceManager:
    """Return the ServiceManager created at startup. Routes receive it through `Manager`."""
    return request.app.state.manager


Manager = Annotated[ServiceManager, Depends(get_manager)]
router = APIRouter()


@router.post("/services", status_code=HTTPStatus.CREATED)
async def add_services(
    services: Annotated[list[ServiceConfig], Body(embed=True, min_length=1)], manager: Manager
) -> list[Service]:
    """Add services, all or none. 409 if a name or host:port already exists, 422 if a service is invalid."""
    return await manager.add_services(services)


@router.get("/services")
async def list_services(manager: Manager) -> list[Service]:
    """List every service with its latest status, sorted by name."""
    return await manager.list_services()


@router.get("/services/{name}")
async def get_service(name: str, manager: Manager) -> Service:
    """Return one service with its latest status. 404 for an unknown name."""
    return await manager.get_service(name)


@router.post("/services/check")
async def check_services(
    manager: Manager, names: Annotated[list[str] | None, Body(embed=True, min_length=1)] = None
) -> list[Service]:
    """Check the named services, or every service when there is no body, concurrently.

    Returns each updated service, sorted by name. 404 if any name is unknown; nothing is checked then.
    """
    return await manager.check_services(names)


@router.post("/services/{name}/check")
async def check_service(name: str, manager: Manager) -> Service:
    """Check the service now and store the result. 200 even when it is DOWN; 404 for an unknown name."""
    return await manager.check_service(name)


@router.get("/services/{name}/history")
async def get_history(
    name: str, manager: Manager, limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)] = DEFAULT_HISTORY_LIMIT
) -> list[ServiceStatus]:
    """Return the service's past checks, newest first. 404 for an unknown name."""
    return await manager.get_history(name, limit)


@router.delete("/services/{name}", status_code=HTTPStatus.NO_CONTENT)
async def delete_service(name: str, manager: Manager) -> None:
    """Delete the service and its history. 404 for an unknown name."""
    await manager.delete_service(name)


async def handle_service_monitor_error(request: Request, error: ServiceMonitorError) -> JSONResponse:
    """Report a domain error with the status and code it declares."""
    return error_response(error.status_code, error.code, str(error))


async def handle_invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    """Report invalid input as 422, listing each problem as {"field", "message"}."""
    # loc looks like ("body", "services", 0, "port") or ("query", "limit"); drop the location to get "services.0.port".
    problems = [
        {"field": ".".join(str(part) for part in problem["loc"][1:]), "message": problem["msg"]}
        for problem in error.errors()
    ]
    return error_response(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_request", problems)


async def handle_http_error(request: Request, error: HTTPException) -> JSONResponse:
    """Report the framework's own errors, such as an unknown URL (404) or method (405), in the same shape."""
    status_code = HTTPStatus(error.status_code)
    return error_response(status_code, status_code.phrase.lower().replace(" ", "_"), error.detail)


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    """Report any other error as 500 without internal details. The server still logs the traceback."""
    return error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "Internal server error")


def error_response(status_code: HTTPStatus, code: str, detail: str | list[dict[str, str]]) -> JSONResponse:
    """Build the one error shape: {"error": code, "detail": a message or a list of problems}."""
    return JSONResponse(status_code=status_code, content={"error": code, "detail": detail})


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the app. Tests pass their own settings; the server reads them from the environment."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Open the database and start monitoring before serving requests; stop both on shutdown."""
        database = Database(settings.database_url)
        await database.create_tables()
        manager = ServiceManager(database, NetworkServiceChecker(settings.check_timeout_seconds))
        app.state.manager = manager
        monitoring = asyncio.create_task(manager.monitor_services(settings.monitor_interval_seconds))
        try:
            yield
        finally:
            monitoring.cancel()
            with suppress(asyncio.CancelledError):  # a cancelled task ends by raising this
                await monitoring
            await database.close()

    app = FastAPI(
        title="Service Monitoring API",
        description="Manage a list of network services and check whether they are reachable over TCP, HTTP(S) or SSH.",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.add_exception_handler(ServiceMonitorError, handle_service_monitor_error)
    app.add_exception_handler(RequestValidationError, handle_invalid_request)
    app.add_exception_handler(HTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
    return app


app = create_app()
