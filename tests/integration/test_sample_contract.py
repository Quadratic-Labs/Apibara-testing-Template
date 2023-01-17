"""This module uses the sample contract tests/assets/sample_contract.cairo to make
sure the integration tests environment is working as expected

If this fails, that most probably means the environment is broken and other tests
will fail for the same reason

## Known environment issues:
1. All indexer tests fail at len(proposals) == 1
Some services are not functioning properly and the indexer is not able to receive
anything, fix by taking down the docker services manually with the following command:

```bash
docker-compose -f docker-compose.test.yml -p indexer-test down
```

Note that theoretically this will only happen if you're passing `--keep-docker-services`
argument to `pytest`
"""
import json
from pathlib import Path

import pymongo
from apibara import EventFilter
from starknet_py.contract import Contract
from starknet_py.net.account.account_client import AccountClient
from starknet_py.utils.data_transformer.data_transformer import CairoSerializer

from ..conftest import IndexerProcessRunner
from .test_utils import default_new_events_handler_test, wait_for_indexer


async def test_contract_abi(sample_contract: Contract, sample_contract_file: Path):
    with open(
        sample_contract_file.parent / "sample_contract_abi.json", encoding="utf8"
    ) as file:
        contract_abi = json.loads(file.read())

    assert sample_contract.data.abi == contract_abi


async def test_invoke(sample_contract: Contract, client: AccountClient):
    amount = 10

    invoke_result = await sample_contract.functions["increase_balance"].invoke(
        amount, max_fee=10**16
    )
    await invoke_result.wait_for_acceptance()

    call_result = await sample_contract.functions["get_balance"].call(client.address)
    assert call_result.res == amount


async def test_event(sample_contract: Contract, client: AccountClient):
    invoke_result = await sample_contract.functions["increase_balance"].invoke(
        10, max_fee=10**16
    )
    await invoke_result.wait_for_acceptance()

    transaction_hash = invoke_result.hash
    transaction_receipt = await client.get_transaction_receipt(transaction_hash)

    # Takes events from transaction receipt
    events = transaction_receipt.events

    # Takes an abi of the event which data we want to serialize
    # We can get it from the contract abi
    emitted_event_abi = {
        "data": [
            {"name": "current_balance", "type": "felt"},
            {"name": "amount", "type": "felt"},
        ],
        "keys": [],
        "name": "increase_balance_called",
        "type": "event",
    }

    # Creates CairoSerializer with contract's identifier manager
    cairo_serializer = CairoSerializer(
        identifier_manager=sample_contract.data.identifier_manager
    )

    # Transforms cairo data to python (needs types of the values and values)
    python_data = cairo_serializer.to_python(
        value_types=emitted_event_abi["data"], values=events[0].data
    )

    # Transforms python data to cairo (needs types of the values and python data)
    event_data = cairo_serializer.from_python(emitted_event_abi["data"], *python_data)
    expected_event_data = ([0, 10], {"current_balance": [0], "amount": [10]})

    assert event_data == expected_event_data

    return transaction_receipt


async def test_indexer(
    run_indexer_process: IndexerProcessRunner,
    sample_contract: Contract,
    client: AccountClient,
    mongo_client: pymongo.MongoClient,
):
    filters = [
        EventFilter.from_event_name(
            name="increase_balance_called",
            address=sample_contract.address,
        ),
    ]

    indexer = run_indexer_process(filters, default_new_events_handler_test)

    transaction_receipt = await test_event(
        sample_contract=sample_contract, client=client
    )

    mongo_db = mongo_client[indexer.id]

    wait_for_indexer(mongo_db, transaction_receipt.block_number)

    events = list(mongo_db["events"].find())
    assert len(events) == 1

    event = events[0]

    assert event["name"] == "increase_balance_called"
    assert int(event["address"].hex(), 16) == sample_contract.address
