# SCITT API emulator

This repository contains the source code for the SCITT API emulator. It is meant to allow experimenting with [SCITT](https://datatracker.ietf.org/wg/scitt/about/) APIs and formats. It is not meant to be used in production code.

## Prerequisites

The emulator assumes a Linux environment with Python 3.8 or higher.
On Ubuntu, run the following to install Python:

```sh
sudo apt install python3.8 python3.8-venv
```

or, if you are running a conda environment, you can get things setup with the following:

```sh
conda env create -f scitt-api-emulator.yml
conda activate scitt
```

## Clone the emulator

Clone the scitt-api-emulator repository and change into the scitt-api-emulator folder:

```sh
git clone https://github.com/scitt-community/scitt-api-emulator.git
```
or for ssh:

```sh
git clone git@github.com:scitt-community/scitt-api-emulator.git
```
then:

```sh
cd scitt-api-emulator
```

## Use the emulator

### Start a fake SCITT service

```sh
./scitt-emulator.sh server --workspace workspace/ --tree-alg CCF
```

`--tree-alg` is currently `CCF` only. 

The default port is 8000 but can be changed with the `--port` argument.

Now the server is running at http://localhost:8000/ and uses `workspace/` to store the service parameters and service state.

The service has the following REST API:

- `POST /entries` - submit a COSE_Sign1 claim to the emulator and return an entry id
- `GET /entries/<entry_id>` - retrieve the COSE_Sign1 claim for the corresponding entry id
- `GET /entries/<entry_id>/receipt` - retrieve the SCITT receipt for corresponding entry id

The following steps should be done from a different terminal, leaving the service running in the background.

### Create claims

```sh
./scitt-emulator.sh client create-claim --issuer did:web:example.com --content-type application/json --payload '{"sun": "yellow"}' --out claim.cose
```

Note: The emulator does not verify claim signatures and generates an ad-hoc key pair to sign the claim.

### Submit claims and retrieve receipts

```sh
./scitt-emulator.sh client submit-claim --claim claim.cose --out claim.receipt.cbor
```

The `submit-claim` command uses the default service URL `http://127.0.0.1:8000` which can be changed with the `--url` argument. It can be used with the built-in server or an external service implementation.

This command sends the following two requests:

1. `POST /entries` with the claim file as HTTP body. The response is JSON containing `"entry_id"`.
2. `GET /entries/<entry_id>/receipt` to retrieve the SCITT receipt.

### Retrieve claims

```sh
./scitt-emulator.sh client retrieve-claim --entry-id 123 --out claim.cose
```

The `retrieve-claim` command uses the default service URL `http://127.0.0.1:8000` which can be changed with the `--url` argument. It can be used with the built-in server or an external service implementation.

This command sends the following request:

- `GET /entries/<entry_id>` to retrieve the claim.

### Retrieve receipts

```sh
./scitt-emulator.sh client retrieve-receipt --entry-id 123 --out receipt.cbor
```

The `retrieve-receipt` command uses the default service URL `http://127.0.0.1:8000` which can be changed with the `--url` argument. It can be used with the built-in server or an external service implementation.

This command sends the following request:

- `GET /entries/<entry_id>/receipt` to retrieve the receipt.

### Validate receipts 

```sh
./scitt-emulator.sh client verify-receipt --claim claim.cose --receipt claim.receipt.cbor --service-parameters workspace/service_parameters.json
```

The `verify-receipt` command verifies a SCITT receipt given a SCITT claim and a service parameters file. This command can be used to verify receipts generated by other implementations.

The `service_parameters.json` file gets created when starting a service using `./scitt-emulator.sh server`. The format of this file is not standardized and is currently:

```json
{
    "serviceId": "emulator",
    "treeAlgorithm": "CCF",
    "signatureAlgorithm": "ES256",
    "serviceCertificate": "-----BEGIN CERTIFICATE-----..."
}
```

`"signatureAlgorithm"` and `"serviceCertificate"` are additional parameters specific to the [`CCF` tree algorithm](https://ietf-scitt.github.io/draft-birkholz-scitt-receipts/draft-birkholz-scitt-receipts.html#name-additional-parameters).

### COSE and CBOR debugging

The following websites can be used to inspect COSE and CBOR files:

- https://gluecose.github.io/cose-viewer/
- https://cbor.me/

## Code structure

`scitt_emulator/scitt.py` contains the core SCITT algorithms that are agnostic of a specific tree algorithm.

`scitt_emulator/ccf.py` is the implementation of the [CCF tree algorithm](https://ietf-scitt.github.io/draft-birkholz-scitt-receipts/draft-birkholz-scitt-receipts.html#name-ccf-tree-algorithm). For each claim, a receipt is generated using a fake but valid Merkle tree that is independent of other submitted claims. A real CCF service would maintain a single Merkle tree covering all submitted claims and auxiliarly entries.

`scitt_emulator/server.py` is a simple Flask server that acts as a SCITT transparency service.

`scitt_emulator/client.py` is a CLI that supports creating claims, submitting claims to and retrieving receipts from the server, and verifying receipts.

In order to add a new tree algorithm, a file like `scitt_emulator/ccf.py` must be created and the containing class be added in `scitt_emulator/tree_algs.py`.

## Run tests

```
./run-tests.sh
```

## Contributing

This project welcomes contributions and suggestions. Please see the [Contribution guidelines](CONTRIBUTING.md).
