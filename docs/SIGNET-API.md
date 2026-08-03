# What the tutorial needs from Bitsaga Signet

The multisig tutorial's coordinator runs on the page. It builds the wallet,
derives the addresses, builds the PSBT and finishes the transaction in the
browser, with no help from any server: `src/web/signet-coordinator.js` is all of
it, and none of it needs a node.

What it cannot do in a browser is see the chain or reach the network. For that it
uses Bitsaga Signet's public API over HTTPS, at `https://signet.bitsaga.be/api`.

## What it uses, and what already exists

| Call | Exists | What the tutorial does with it |
| --- | --- | --- |
| `GET /status` | yes | how old the last block is, which is what the progress line follows while waiting for a confirmation |
| `POST /claim` | yes | asks the faucet to pay the wallet's first address |
| `GET /tx-proof?txid=` | yes | two jobs at once: it answers 404 until the transaction is in a block, so it *is* the confirmation check, and it returns the raw transaction, which is where the coordinator finds the output it is about to spend and what that output is worth |
| `POST /broadcast` | **no** | sends the finished transaction |

Three of the four are already there and needed nothing. The fourth does not
exist, and there is no way to spend without it: a signed transaction has to
reach a node, and nothing else on this page can carry it.

## The one endpoint to add

**`POST /api/broadcast`**, body `{"tx": "<raw transaction hex>"}`, answering
`{"ok": true, "txid": "<64 hex characters>"}`.

It is a handful of lines in `bitsaga/services/signet/faucet/faucet.py`, in the
same shape as the endpoints beside it:

- Route it in `do_POST`, next to `/claim`, since that method already reads and
  bounds a JSON body.
- Refuse anything that is not hex, and cap the length. A raw transaction from
  this tutorial is about 400 bytes; a few kilobytes is a generous ceiling and
  it stops the endpoint being a way to post arbitrary bulk at the node.
- Send it with **Fulcrum**, not the node: `Electrum.call([("blockchain.transaction.broadcast", [raw])])`
  is one line and keeps this on the same read-mostly path as `/tx-proof`, so the
  node's RPC whitelist does not have to grow a method that spends. Fulcrum
  refuses an invalid transaction with the node's own reason, which is the right
  error to pass back.
- Rate limit it the way `/claim` is rate limited, by IP, through the same
  ledger. A faucet that hands out coins and an endpoint that relays spends of
  those coins are the same surface.
- Same CORS as the rest, so `https://bitsaga.be` may call it.

Nothing else changes: no nginx, no ports, no certificates, no DNS. It is a new
route on a service that is already listening, already proxied and already
allowed from that one origin.

## Until it exists

`test/test_tutorial_live.py` answers that one request itself, by handing the
transaction to Fulcrum with exactly the call above (`test/signet_bridge.py`).
The transaction really is broadcast, to the real chain, and the confirmation
that follows is read back from the public API like everything else. The page's
own code is not aware of the difference: it posts to the same URL either way.
That server is not reachable from anywhere public, so the test needs
`SIGNET_ELECTRUM=host:port`, and where it listens is not written down in this
repository.
