"""Viseron webserver."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
from typing import TYPE_CHECKING

import tornado.gen
import tornado.ioloop
import tornado.web
import voluptuous as vol
from tornado.routing import PathMatches

from viseron.components.webserver.auth import Auth
from viseron.const import EVENT_DOMAIN_REGISTERED, VISERON_SIGNAL_SHUTDOWN
from viseron.domains.camera.const import DOMAIN as CAMERA_DOMAIN
from viseron.exceptions import ComponentNotReady
from viseron.helpers.storage import Storage
from viseron.helpers.validators import CoerceNoneToDict

from .api import APIRouter
from .const import (
    COMPONENT,
    CONFIG_AUTH,
    CONFIG_DAYS,
    CONFIG_DEBUG,
    CONFIG_HOURS,
    CONFIG_MINUTES,
    CONFIG_PORT,
    CONFIG_SESSION_EXPIRY,
    DEFAULT_COMPONENT,
    DEFAULT_DAYS,
    DEFAULT_DEBUG,
    DEFAULT_HOURS,
    DEFAULT_MINUTES,
    DEFAULT_PORT,
    DEFAULT_SESSION_EXPIRY,
    DESC_COMPONENT,
    DESC_DEBUG,
    DESC_PORT,
    PATH_STATIC,
    WEBSERVER_STORAGE_KEY,
    WEBSOCKET_COMMANDS,
)
from .not_found_handler import NotFoundHandler
from .request_handler import ViseronRequestHandler
from .stream_handler import DynamicStreamHandler, StaticStreamHandler
from .websocket_api import WebSocketHandler
from .websocket_api.commands import (
    get_cameras,
    get_config,
    get_entities,
    ping,
    restart_viseron,
    save_config,
    subscribe_event,
    unsubscribe_event,
)

if TYPE_CHECKING:
    from viseron import Event, Viseron
    from viseron.domains.camera import AbstractCamera


LOGGER = logging.getLogger(__name__)


CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(
            COMPONENT, default=DEFAULT_COMPONENT, description=DESC_COMPONENT
        ): vol.All(
            CoerceNoneToDict(),
            {
                vol.Optional(
                    CONFIG_PORT, default=DEFAULT_PORT, description=DESC_PORT
                ): vol.All(int, vol.Range(min=1024, max=49151)),
                vol.Optional(
                    CONFIG_DEBUG, default=DEFAULT_DEBUG, description=DESC_DEBUG
                ): bool,
                vol.Optional(CONFIG_AUTH): vol.All(
                    CoerceNoneToDict(),
                    {
                        vol.Optional(
                            CONFIG_SESSION_EXPIRY, default=DEFAULT_SESSION_EXPIRY
                        ): vol.All(
                            CoerceNoneToDict(),
                            {
                                vol.Optional(
                                    CONFIG_DAYS, default=DEFAULT_DAYS
                                ): vol.All(int, vol.Range(min=0)),
                                vol.Optional(
                                    CONFIG_HOURS, default=DEFAULT_HOURS
                                ): vol.All(int, vol.Range(min=0)),
                                vol.Optional(
                                    CONFIG_MINUTES, default=DEFAULT_MINUTES
                                ): vol.All(int, vol.Range(min=0)),
                            },
                        )
                    },
                ),
            },
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def setup(vis: Viseron, config):
    """Set up the webserver component."""
    config = config[COMPONENT]
    webserver = Webserver(vis, config)
    vis.register_signal_handler(VISERON_SIGNAL_SHUTDOWN, webserver.stop)

    webserver.register_websocket_command(ping)
    webserver.register_websocket_command(subscribe_event)
    webserver.register_websocket_command(unsubscribe_event)
    webserver.register_websocket_command(get_cameras)
    webserver.register_websocket_command(get_config)
    webserver.register_websocket_command(save_config)
    webserver.register_websocket_command(restart_viseron)
    webserver.register_websocket_command(get_entities)

    webserver.start()

    return True


class IndexHandler(ViseronRequestHandler):
    """Handler for index page."""

    def get(self):
        """GET request."""
        self.render(os.path.join(PATH_STATIC, "index.html"))


class DeprecatedStreamHandler(tornado.web.RequestHandler):
    """Socket handler."""

    def get(self, camera):
        """GET request."""
        LOGGER.warning(
            f"The endpoint /{camera}/stream is deprecated. "
            f"Please use /{camera}/mjpeg-stream instead."
        )
        self.redirect(f"/{camera}/mjpeg-stream")


class WebserverStore:
    """Webserver storage."""

    def __init__(self, vis: Viseron):
        self._store = Storage(vis, WEBSERVER_STORAGE_KEY)
        self._data = self._store.load()

    @property
    def cookie_secret(self):
        """Return cookie secret."""
        if "cookie_secret" not in self._data:
            self._data["cookie_secret"] = secrets.token_hex(64)
            self._store.save(self._data)
        return self._data["cookie_secret"]


def create_application(vis: Viseron, config, cookie_secret):
    """Return tornado web app."""
    application = tornado.web.Application(
        [
            (
                r"/(?P<camera>[A-Za-z0-9_]+)/mjpeg-stream",
                DynamicStreamHandler,
                {"vis": vis},
            ),
            (
                (
                    r"/(?P<camera>[A-Za-z0-9_]+)/mjpeg-streams/"
                    r"(?P<mjpeg_stream>[A-Za-z0-9_\-]+)"
                ),
                StaticStreamHandler,
                {"vis": vis},
            ),
            (
                (
                    r"/(?P<camera>[A-Za-z0-9_]+)/static-mjpeg-streams/"
                    r"(?P<mjpeg_stream>[A-Za-z0-9_\-]+)"
                ),
                StaticStreamHandler,
                {"vis": vis},
            ),
            (r"/websocket", WebSocketHandler, {"vis": vis}),
            (r"/.*", IndexHandler, {"vis": vis}),
        ],
        default_handler_class=NotFoundHandler,
        static_path=PATH_STATIC,
        websocket_ping_interval=10,
        debug=config[CONFIG_DEBUG],
        cookie_secret=cookie_secret,
        xsrf_cookies=True,
    )
    application.add_handlers(
        r".*",
        [
            (PathMatches(r"/api/.*"), APIRouter(vis, application)),
        ],
    )
    return application


class Webserver(threading.Thread):
    """Webserver."""

    def __init__(self, vis: Viseron, config):
        super().__init__(name="Tornado Webserver", daemon=True)
        self._vis = vis
        self._config = config
        self._auth = None
        if self._config.get(CONFIG_AUTH, False):
            self._auth = Auth(vis, config)
        self._store = WebserverStore(vis)

        vis.data[COMPONENT] = self
        vis.data[WEBSOCKET_COMMANDS] = {}

        ioloop = asyncio.new_event_loop()
        asyncio.set_event_loop(ioloop)
        self.application = create_application(vis, config, self._store.cookie_secret)
        try:
            self.application.listen(
                config[CONFIG_PORT],
                xheaders=True,
            )
        except OSError as error:
            if "Address already in use" in str(error):
                raise ComponentNotReady from error
            raise error
        self._ioloop = tornado.ioloop.IOLoop.current()

        self._vis.listen_event(
            EVENT_DOMAIN_REGISTERED.format(domain=CAMERA_DOMAIN), self.camera_registered
        )

    @property
    def auth(self):
        """Return auth."""
        return self._auth

    def register_websocket_command(self, handler):
        """Register a websocket command."""
        if handler.command in self._vis.data[WEBSOCKET_COMMANDS]:
            LOGGER.error(f"Command {handler.command} has already been registered")
            return

        self._vis.data[WEBSOCKET_COMMANDS][handler.command] = (handler, handler.schema)

    def _serve_camera_recordings(self, camera: AbstractCamera):
        """Serve recordings of each camera in a static file handler."""
        self.application.add_handlers(
            r".*",
            [
                (
                    rf"/recordings/{camera.identifier}/(.*/.*)",
                    tornado.web.StaticFileHandler,
                    {"path": camera.recorder.recordings_folder},
                )
            ],
        )

    def camera_registered(self, event_data: Event):
        """Handle camera registering."""
        camera: AbstractCamera = event_data.data
        self._serve_camera_recordings(camera)

    def run(self):
        """Start ioloop."""
        self._ioloop.start()
        self._ioloop.close()

    def stop(self):
        """Stop ioloop."""
        LOGGER.debug("Stopping webserver")
        self._ioloop.stop()
