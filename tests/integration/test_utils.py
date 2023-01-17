import logging
import os
from pathlib import Path

import backoff
import requests
from apibara import IndexerRunner, Info, NewEvents
from apibara.indexer import IndexerRunnerConfiguration
from apibara.model import BlockHeader, EventFilter, StarkNetEvent
from grpc.aio import AioRpcError
from grpc_requests.aio import AsyncClient
from pymongo.database import Database
from python_on_whales import Container, DockerClient
from starknet_py.net.gateway_client import GatewayClient

from ..conftest import config

logger = logging.getLogger("tests")

docker = DockerClient(
    compose_files=["docker-compose.test.yml"],
    compose_project_directory=Path(__file__).parent.parent,
    compose_project_name="apibara-testing-template",
)


def raise_timeout_error(details: dict):
    # TODO: better error message
    raise TimeoutError(details["target"].__name__)


# backoff will run the function until no container is restarting
# if a container is not running (exited, paused, stopped) a RuntimeError will be raised
@backoff.on_predicate(
    backoff.constant,
    max_time=config.default_max_backoff_time,
    on_giveup=raise_timeout_error,
)
async def wait_for_docker_services():
    containers = docker.compose.ps()

    not_running_containers: list[Container] = []
    restarting_containers: list[Container] = []

    RUNNING = "running"
    RESTARTING = "restarting"

    for container in containers:
        if container.state.status == RESTARTING:
            restarting_containers.append(container)
        elif container.state.status != RUNNING:
            not_running_containers.append(container)

    if not_running_containers:
        not_running_containers_str = ",".join(
            [
                f"{container.name}={container.state.status}"
                for container in not_running_containers
            ]
        )
        raise RuntimeError(
            f"Some containers are not running: {not_running_containers_str}"
        )

    return not restarting_containers


# TODO: use a more specific exception than RequestException
@backoff.on_exception(
    backoff.constant,
    requests.RequestException,
    max_time=config.default_max_backoff_time,
)
async def wait_for_devnet():
    # TODO: think about using an async client here
    url = os.path.join(config.starknet_network_url, "is_alive")
    is_alive_response = requests.get(url, timeout=1)
    is_alive_response.raise_for_status()


# TODO: use a more specific exception than RequestException
@backoff.on_exception(
    backoff.constant,
    requests.RequestException,
    max_time=config.default_max_backoff_time,
)
async def wait_for_graphql() -> bool:
    # TODO: use gql client instead
    url = f"http://{config.graphql_url }/graphql?query=%7B__typename%7D"
    response = requests.get(url, timeout=1)
    response.raise_for_status()
    return True


@backoff.on_exception(
    backoff.constant, AioRpcError, max_time=config.default_max_backoff_time
)
async def wait_for_apibara():
    grpc_client = AsyncClient(config.apibara_server_url, ssl=False)
    node_service = await grpc_client.service("apibara.node.v1alpha1.Node")
    return await node_service.Status()


@backoff.on_predicate(
    backoff.constant,
    max_time=config.wait_for_indexer_backoff_time,
    on_giveup=raise_timeout_error,
)
def wait_for_indexer(mongo_db: Database, block_number: int):
    """Wait for the indexer until it reaches a given block_number

    It could be used during tests to ensure some events were processed before
    reading the mongo db
    """
    if document := mongo_db["_apibara"].find_one({"indexer_id": mongo_db.name}):
        if indexed_to := document["indexed_to"]:
            logger.debug(
                "Waiting for indexer to reach block_number=%s, it's now at"
                " block_number=%s",
                block_number,
                indexed_to,
            )
            return indexed_to >= block_number


async def default_new_events_handler_test(info: Info, block_events: NewEvents):
    """Handle a group of events grouped by block."""
    logger.debug("Received events for block %s", block_events.block.number)
    events = [
        {
            "timestamp": block_events.block.timestamp,
            "transaction_hash": event.transaction_hash,
            "address": event.address,
            "data": event.data,
            "name": event.name,
        }
        for event in block_events.events
    ]

    logger.debug("Writing to 'events' collection: %s", events)

    # Insert multiple documents in one call.
    await info.storage.insert_many("events", events)


# pylint: disable=too-many-arguments
async def run_indexer(
    server_url,
    mongo_url,
    starknet_network_url,
    filters: list[EventFilter],
    ssl=True,
    restart: bool = False,
    indexer_id: str = config.indexer_id,
    new_events_handler=default_new_events_handler_test,
):
    logger.info(
        "Starting the indexer with server_url=%s, mongo_url=%s,"
        " starknet_network_url=%s, indexer_id=%s, restart=%s, ssl=%s, filters=%s",
        server_url,
        mongo_url,
        starknet_network_url,
        indexer_id,
        restart,
        ssl,
        filters,
    )

    runner = IndexerRunner(
        config=IndexerRunnerConfiguration(
            apibara_url=server_url,
            apibara_ssl=ssl,
            storage_url=mongo_url,
        ),
        reset_state=restart,
        indexer_id=indexer_id,
        new_events_handler=new_events_handler,
    )

    runner.set_context(
        {
            "starknet_network_url": starknet_network_url,
            "starknet_client": GatewayClient(starknet_network_url),
        }
    )

    # Create the indexer if it doesn't exist on the server,
    # otherwise it will resume indexing from where it left off.
    #
    # For now, this also helps the SDK map between human-readable
    # event names and StarkNet events.
    runner.create_if_not_exists(filters=filters)

    logger.info("Initialization completed. Entering main loop.")

    await runner.run()
