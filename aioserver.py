"""A simple http backend for communicating with a TANGO control system

The idea is that each client establishes a websocket connection with
this server (on /socket), and sets up a number of subscriptions to
TANGO attributes.  The server keeps track of changes to these
attributes and sends events to the interested clients. The server uses
Taurus for this, so polling, sharing listeners, etc is handled "under
the hood".

There is also a GraphQL endpoint (/db) for querying the TANGO database.
"""

from collections import defaultdict
import json
import logging
import time
from weakref import WeakSet, WeakValueDictionary

import aiohttp
from aiohttp import web
import asyncio
from asyncio import Queue, QueueEmpty
import PyTango
from PyTango import (DeviceProxy, DeviceAttributeConfig, DeviceAttribute,
                     EventType, DevFailed)

from schema import tangoschema
from listener import TaurusWebAttribute


def serialize(events, protocol="json"):
    "Returns event data in a serialized form according to a protocol"
    if protocol == "json":
        # default protocol; simplest, human readable, but also very inefficient
        # in particular for spectrum/image data
        return json.dumps({"events": events})
    elif protocol == "bson":
        # "Binary JSON" protocol. A lot more space efficient than
        # encoding as JSON, especially for float values and arrays.
        # There's very little size overhead.
        # Have not looked into encoding performance.
        return bson.dumps({"events": events})
    raise ValueError("Unknown protocol '%s'" % protocol)


@asyncio.coroutine
def consumer(keeper, ws):

    """A coroutine that sends out any accumulated events once
    per second."""

    while True:
        # check for new events once a second
        yield from asyncio.sleep(1)
        events = keeper.get()
        # if not events:
        #     # no events were collected
        #     continue
        try:
            data = serialize(events, ws.protocol)
            if isinstance(data, bytes):
                ws.send_bytes(data)
            else:
                ws.send_str(data)
            logging.debug("sent %d bytes", len(data))
        except RuntimeError as e:
            # guess the client must be gone. Maybe there's a neater
            # way to detect this.
            logging.warn(e)
            break
    logging.info("Sender for %r exited" % ws)


class EventKeeper:

    """A simple wrapper that keeps the latest event values for
    each attribute"""

    def __init__(self):
        self._events = defaultdict(dict)
        self._timestamps = defaultdict(dict)
        self._latest = defaultdict(dict)

    def put(self, model, action, value):
        "Update a model"
        self._events[action][model] = value
        self._timestamps[action][model] = time.time()

    def get(self):
        "Returns the latest accumulated events"
        tmp, self._events = self._events, defaultdict(dict)
        for event_type, events in tmp.items():
            self._latest[event_type].update(events)
        return tmp


@asyncio.coroutine
def handle_websocket(request):

    "Handles a websocket to a client over its lifetime"

    ws = web.WebSocketResponse(protocols=("json", "bson"))
    yield from ws.prepare(request)

    logging.info("Listener has connected; protocol %s" % ws.protocol)

    keeper = EventKeeper()
    loop = asyncio.get_event_loop()
    loop.create_task(consumer(keeper, ws))
    listeners = {}

    # wait for messages over the socket
    # A message must be JSON, in the format:
    #   {"type": "SUBSCRIBE", "models": ["sys/tg_test/double_scalar"]}
    # where "type" can be "SUBSCRIBE" or "UNSUBSCRIBE" and models is a list of
    # device attributes.
    while True:
        msg = yield from ws.receive()
        print(msg)
        try:
            if msg.tp == aiohttp.MsgType.text:
                action = json.loads(msg.data)
                logging.debug("ws got %r", action)
                if action["type"] == 'SUBSCRIBE':
                    for attr in action["models"]:
                        listener = TaurusWebAttribute(attr, keeper)
                        listeners[attr] = listener
                        logging.debug("add listener for '%s'", attr)
                elif action["type"] == "UNSUBSCRIBE":
                    for attr in action["models"]:
                        logging.debug("remove listener for '%s'", attr)
                        listener = listeners.pop(attr, None)
                        if listener:
                            listener.clear()
            elif msg.tp == aiohttp.MsgType.error:
                logging.warn('websocket closed with exception %s',
                             ws.exception())
        except RuntimeError as e:
            logging.warn("websocket died: %s", e)

    # wipe all the client's subscriptions
    for listener in listeners.values():
        listener.clear()
    listeners.clear()

    logging.info('websocket connection %s closed' % ws)

    return ws


@asyncio.coroutine
def db_handler(request):
    "serve GraphQL queries"
    post_data = yield from request.json()
    query = post_data["query"]
    loop = asyncio.get_event_loop()  # TODO: this looks stupid
    try:
        # guess we wouldn't have to do this if the client was async...
        result = yield from loop.run_in_executor(
            None, tangoschema.execute, query)
        data = (json.dumps({"data": result.data or {}}, indent=4))
        return web.Response(body=data.encode("utf-8"),
                            content_type="application/json")
    except Exception as e:
        print(e)


if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)

    app = aiohttp.web.Application(debug=True)

    app.router.add_route('GET', '/socket', handle_websocket)
    app.router.add_route('POST', '/db', db_handler)
    app.router.add_static('/', 'static')

    loop = asyncio.get_event_loop()
    handler = app.make_handler(debug=True)
    f = loop.create_server(handler, '0.0.0.0', 5004)
    logging.info("Point your browser to http://localhost:5003/index.html")
    srv = loop.run_until_complete(f)
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("Ctrl-C was pressed")
    finally:
        srv.close()
        loop.run_until_complete(srv.wait_closed())
        loop.run_until_complete(handler.finish_connections(1.0))
        loop.run_until_complete(app.finish())

    loop.close()
