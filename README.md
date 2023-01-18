# Apibara Testing Template

<details open ="open">
<summary>Table of Contents</summary>

- [About](#about)
- [Getting Started](#getting-started)

</details>

# About

>Set of services, configurations & pytest features to test an Apibara indexer

Our [Moloch on Starknet indexer](https://github.com/Quadratic-Labs/Moloch-on-Starknet-indexer) is based on [Apibara python indexer Template](https://github.com/apibara/python-indexer-template), which enables to quickly start indexing smart contracts events with [Apibara](https://github.com/apibara/apibara).

In the course of the testing phase of our Apibara indexer, we built an environment with all the needed services and configurations (see below), also including all the pytest features to manage the tests.

```
Indexer -> Apibara Node -> Devnet
       |
        -> Mongodb
```

# Getting Started

Create a new virtual environment for this project. While this step is not required, it is _highly recommended_ to avoid conflicts between different installed packages.

    python3 -m venv venv

Then activate the virtual environment.

    source venv/bin/activate

Then install `poetry` and use it to install the package dependencies.

    python3 -m pip install poetry
    poetry install

Run the tests

    pytest

You can also pass `--keep-docker-services` param to avoid creating and destroying docker services each time, this will improve the tests execution time if you run it multiple times successively

    pytest --keep-docker-services
