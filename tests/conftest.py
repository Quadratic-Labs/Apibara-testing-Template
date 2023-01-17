# pylint: disable=redefined-outer-name,unused-argument
import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from multiprocessing import Process
from pathlib import Path
from typing import Any, Callable

import mongomock
import pytest
import requests
from apibara import EventFilter
from pymongo import MongoClient
from pymongo.database import Database
from pytest import Item, MonkeyPatch
from python_on_whales import Container
from starknet_py.compile.compiler import Compiler
from starknet_py.contract import Contract
from starknet_py.net.account.account_client import AccountClient
from starknet_py.net.gateway_client import GatewayClient
from starknet_py.net.models import StarknetChainId
from starknet_py.net.signer.stark_curve_signer import KeyPair

from . import config
from .integration.test_utils import (
    default_new_events_handler_test,
    docker,
    run_indexer,
    wait_for_apibara,
    wait_for_devnet,
    wait_for_docker_services,
)


# TODO: move the logs configuration to a config file
def disable_annoying_debug_loggers():
    loggers_names = ["grpc", "grpc_requests.aio", "apibara", "asyncio"]
    for logger_name in loggers_names:
        logging.getLogger(logger_name).setLevel(logging.INFO)


def pytest_sessionstart():
    disable_annoying_debug_loggers()


def pytest_collection_modifyitems(items: list[Item]):
    """Mark all tests that uses docker services as slow"""
    for item in items:
        if "docker_compose_services" in getattr(item, "fixturenames", ()):
            item.add_marker("slow")


@dataclass
class Indexer:
    process: Process
    id: str


IndexerProcessRunner = Callable[[list[EventFilter], Any], Indexer]


def pytest_addoption(parser):
    parser.addoption("--keep-docker-services", action="store_true", default=False)


@dataclass
class Account:
    address: int
    private_key: int
    public_key: int


# We have to override the default event_loop to be able to write async fixtures
# with a session scope
# TODO: think about using a different event_loop instead of overriding as
# suggested by the asyncio-pytest docs
@pytest.fixture(scope="session")
def event_loop():
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def docker_compose_services(request: pytest.FixtureRequest) -> list[Container]:
    docker.compose.up(detach=True)

    # This ensures the docker services are deleted even if the fixture raises an exception
    # Pass --keep-docker-services to the pytest command to prevent taking them down,
    # which will make integration tests run faster
    if not request.config.option.keep_docker_services:
        request.addfinalizer(docker.compose.down)

    await wait_for_docker_services()

    # Run the wait functions concurrently using asyncio tasks
    wait_for_devnet_task = asyncio.create_task(wait_for_devnet())
    wait_for_apibara_task = asyncio.create_task(wait_for_apibara())

    await wait_for_devnet_task
    await wait_for_apibara_task

    return docker.compose.ps()


@pytest.fixture(scope="session")
def predeployed_accounts() -> list[Account]:
    url = os.path.join(config.starknet_network_url, "predeployed_accounts")
    accounts = requests.get(url, timeout=5).json()

    return [
        Account(
            address=int(account["address"], 16),
            private_key=int(account["private_key"], 16),
            public_key=int(account["public_key"], 16),
        )
        for account in accounts
    ]


@pytest.fixture(scope="session")
def client(
    docker_compose_services, predeployed_accounts: list[Account]
) -> AccountClient:
    client = GatewayClient(config.starknet_network_url)

    account = predeployed_accounts[0]

    return AccountClient(
        address=account.address,
        client=client,
        # I don't know why we have to pass the chain_id/chain
        chain=StarknetChainId.TESTNET,
        key_pair=KeyPair(account.private_key, account.public_key),
        # Version 0 (default) is deprecated, and raises exception when invoking get_nonce
        supported_tx_version=1,
    )


@pytest.fixture(scope="session")
def sample_contract_file() -> Path:
    tests_dir = Path(__file__).parent
    contract_file = tests_dir / "assets/sample_contract.cairo"
    return contract_file


@pytest.fixture(scope="session")
def contract_file() -> Path:
    root_dir = Path(__file__).parent.parent
    contract_file = root_dir / "cairo-contracts/contracts/main.cairo"
    return contract_file


@pytest.fixture
async def sample_contract(
    client: AccountClient, sample_contract_file: Path
) -> Contract:
    deployment_result = await Contract.deploy(
        client=client,
        compilation_source=[str(sample_contract_file)],
    )
    await deployment_result.wait_for_acceptance()
    return deployment_result.deployed_contract


@pytest.fixture(scope="session")
async def compiled_contract(contract_file: Path) -> str:
    return Compiler(
        contract_source=[str(contract_file)],
        cairo_path=[str(contract_file.parent)],
    ).compile_contract()


@pytest.fixture
async def contract(client: AccountClient, compiled_contract: str) -> Contract:
    majority = 50
    quorum = 60
    grace_duration = 10
    voting_duration = 10

    deployment_result = await Contract.deploy(
        client=client,
        compiled_contract=compiled_contract,
        constructor_args=[majority, quorum, grace_duration, voting_duration],
    )
    await deployment_result.wait_for_acceptance()
    return deployment_result.deployed_contract


@pytest.fixture
async def contract_events(contract: Contract) -> dict:
    events = {}

    for element in contract.data.abi:
        if element["type"] == "event":
            events[element["name"]] = element

    return events


@pytest.fixture
def run_indexer_process(
    docker_compose_services, request: pytest.FixtureRequest
) -> IndexerProcessRunner:
    def _create_indexer(
        filters: list[EventFilter], new_events_handler=default_new_events_handler_test
    ) -> Indexer:
        indexer_id = (
            request.function.__name__
            + "_"
            + datetime.now().strftime("%d%bT%Hh%Im%Ms%f")[:-3]
        )
        process = Process(
            target=lambda: asyncio.run(
                run_indexer(
                    server_url=config.apibara_server_url,
                    mongo_url=config.mongo_url,
                    starknet_network_url=config.starknet_network_url,
                    ssl=False,
                    indexer_id=indexer_id,
                    new_events_handler=new_events_handler,
                    filters=filters,
                    restart=True,
                ),
            )
        )
        process.start()
        request.addfinalizer(process.terminate)

        return Indexer(process, indexer_id)

    return _create_indexer


@pytest.fixture(scope="session")
def mongo_client(docker_compose_services) -> MongoClient:
    return MongoClient(config.mongo_url, tz_aware=True)


@pytest.fixture
def mongomock_client(monkeypatch: MonkeyPatch) -> MongoClient:
    monkeypatch.setenv("USING_MONGOMOCK", "true")
    return mongomock.MongoClient(config.mongo_url, tz_aware=True)


@pytest.fixture
def mongo_db(request: pytest.FixtureRequest, mongo_client: MongoClient) -> Database:
    db_name = (
        request.function.__name__
        + "_"
        + datetime.now().strftime("%d%bT%Hh%Im%Ms%f")[:-3]
    )

    yield mongo_client[db_name]

    mongo_client.drop_database(db_name)
