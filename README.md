# Apibara Testing Template

<details open ="open">
<summary>Table of Contents</summary>

- [About](#about)
- [Story](#story)
- [Getting Started](#getting-started)

</details>

# About
All the services and configurations you need to test your Apibara indexer

# Story
While testing our Apibara indexer, we built an environment with all the needed services and configurations (see below), in addition to all the pytest fixtures and options to run them, tear them down, wait for them to be ready .... We realized this is a common issue for all Apibara indexers, our environment is generic and could be used for other use-cases that's why we're open sourcing it for the Web3 community


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
