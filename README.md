# NERVA · Portable Decision-Gating Kit

Five files. One discipline.

## What this is

A way to invoke NERVA from any language model — Claude, ChatGPT, Gemini — without leaving the chat. Paste the prompt once, drop in a screenshot or description of a decision you are considering, and NERVA returns a structured verdict in the same visual language every time. Log the receipt to the ledger. After thirty receipts, you have a calibration story.

Built originally for Kalshi trade gating, but the prompt is domain-agnostic. Works on any decision that has stakes, a time horizon, and a thesis you can articulate.

## The five files

| File | What it is |
|---|---|
| `NERVA_DECISION_PROMPT.md` | The prompt. Paste into a Claude Project (Project Instructions field) or a new ChatGPT/Gemini chat. From then on, anything you send gets scored against the same kernel. |
| `nerva_kernel.py` | The reference implementation. Verifies what the LLM produced. Run `python nerva_kernel.py --example` to see it work, or pipe a JSON payload to stdin. |
| `nerva-verdict-card.html` | The visual output template, populated with the Antonelli example. Shows what a verdict looks like. The prompt instructs the LLM to render in this language. |
| `nerva-ledger.html` | The receipt store. Browser-local (no server, no account). Holds every receipt, computes the running lift score, tracks resolution outcomes. Export to JSON to back up or move between devices. |
| `README.md` | This file. |

## How to use it (the workflow)

1. **Once.** Open Claude. Start a new Project called *NERVA Decisions* (or similar). Paste the contents of `NERVA_DECISION_PROMPT.md` into the Project Instructions field. Done — this persists.
2. **Every time you face a decision.** In any chat inside that Project, paste a screenshot or describe the decision. Answer the four required questions when prompted (thesis, evidence, falsifier, why now). NERVA returns a verdict rendered in the verdict-card aesthetic.
3. **Verify the math (optional).** For decisions that matter, re-run the same inputs through `nerva_kernel.py` and confirm C and τ agree with what the LLM produced.
4. **Log the receipt.** Open `nerva-ledger.html`. Click *Add receipt*. Paste the JSON. The receipt is now in your ledger.
5. **Close the loop.** When the decision resolves, return to the ledger, mark the outcome (Win / Loss / N/A) and P&L. The calibration panel updates automatically.

## Deployment modes

NERVA inputs are often sensitive. Choose the mode that matches what you intend to score.

**Public LLM** — paste the prompt into Claude.ai or ChatGPT. Convenient. Right for: public-market trades, personal decisions you would discuss with a friend, anything non-confidential.

**API with your own account** — run NERVA via your provider's API with data retention disabled in account settings. Right for: business decisions involving real client names, internal financials, sensitive strategy.

**Self-hosted** — run `nerva_kernel.py` directly with manually-supplied confidence inputs. No LLM in the loop. Right for: regulated industries (legal, healthcare, finance, defense) and any decision with real confidentiality obligations.

Operational rules regardless of mode:

- **Turn off training-data use** in your LLM provider's settings before pasting NERVA.
- **Scrub identifiers** — replace named third parties with role labels.
- **Use a Project, not a default chat** on Claude.ai, to isolate context.
- **Keep the ledger sovereign** — `nerva-ledger.html` stores receipts in your browser's localStorage, not on a server. Export to JSON to back up.

## Why this exists

NERVA-as-a-website requires people to land on a URL, learn an input format, type into specific fields. NERVA-as-a-prompt lives wherever a user already has a language model open. Distribution is one paste. The kernel becomes a discipline, not an app.

The verdict card and the ledger are the connective tissue: one defines the consistent visual language of every NERVA output, the other makes the receipts accumulate into a calibration story over time.

## Notes

The kernel is conservative by design. Most verdicts will be **WAIT**. That is the correct behavior of a brake. **COMMIT** requires high confidence *and* low entropy *and* the brake disengaged — a combination that is rare unless you genuinely have a specific, differentiated, falsifiable thesis with structural timing.

The lift score (hit rate when followed minus hit rate when overrode) becomes meaningful at thirty resolved decisions with at least ten overrides. Below that threshold it is suggestive; do not publish it as evidence.

— Starpoint LLC · NERVA v11 · patent pending
