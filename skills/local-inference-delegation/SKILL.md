---
name: "local-inference-delegation"
description: "Delegate bulk, mechanical, or privacy-sensitive work to a local Ollama model via the `ollama-delegate` MCP server instead of doing it inline. Use this whenever a task involves processing many items at once (classifying, tagging, summarising, extracting, reformatting across dozens or hundreds of files, notes, rows or messages), whenever content should not leave the machine, or whenever embeddings or semantic search are needed. Also use it when the operator says \"use the local model\", \"delegate this\", \"run this locally\", \"offload this\", or mentions Ollama, local LLMs, reranking, or a specific model name. Trigger even when the request does not mention local models at all — a large mechanical job is itself the signal, and the most common failure is forgetting this option exists and grinding through the work inline."
---

# Local inference delegation

The operator runs Ollama on their own machine, and the `ollama-delegate` MCP
server exposes it as tools. This skill is about using those well.

**Go by the tools, not by the server's name.** `ollama-delegate` is what the
project is called; the name it is registered under is whatever the operator put
in their client config, and it varies. If you can call `list_models`, `generate`,
`chat` and `embed`, you are in the right place.

**The failure this exists to prevent is not misuse — it is non-use.** Faced with
"classify these 200 notes", the default behaviour is to just start classifying,
burning context and time on work a 7B model would do acceptably in one call. The
option has to be considered *before* starting, because once you are 40 items in
it is too late to be worth switching.

## Is this a delegation job?

Ask three questions. Any single "yes" is enough.

| Question | Why it matters |
|---|---|
| **Is it bulk and mechanical?** Same judgement applied many times — tagging, classifying, extracting a field, normalising formats | Volume is what makes delegation pay. One item is not worth the round trip |
| **Should this content stay on the machine?** Personal notes, health data, genealogy, anything the operator would not paste into a web form | A local model is the only option that makes this true. This alone justifies delegating even a small job |
| **Are embeddings needed?** Semantic search, similarity, clustering | There is no inline alternative. `embed` is the only route |

**Do not delegate** work needing real reasoning, judgement across a whole corpus,
or anything where you would have to check every result anyway — verifying 200
outputs costs more than producing them. Local models are good at *applying a rule*
and unreliable at *deciding what the rule should be*. Decide the rule yourself,
then delegate its application.

## Start here, every time

```
list_models(include_details=true)
```

Never assume a model is installed, and never assume its context window. Both are
host-specific and both change. A parameter count is not a context length —
`nomic-embed-text` is 137M parameters and 2,048 tokens; `gemma4:12b` is 11.9B
parameters and 262,144 tokens.

The response gives `location`, `context_length` and `capabilities` per model.
Those three fields drive every decision below.

## Choosing a model

**`list_models` is the routing table.** It is live, it is authoritative, and it
is one call away at any moment — so there is nothing to look up, nothing to keep
in sync, and no file whose absence degrades anything. The ladder below needs only
`capabilities` and `context_length` from the call above.

**Do not go looking for a document that says which model to use.** A written
roster is stale the moment someone pulls or deletes a model, and the host will
tell you the truth for free. If the operator happens to keep notes of their own,
they will point you at them; treat that as a shortcut to a measurement, never as
the authority. **The host outranks the note.**

Work down this list; stop at the first match.

1. **Embeddings** → the model with `embedding` in `capabilities`. There is
   normally exactly one. It is *not* interchangeable with a chat model.
2. **Fill-in-middle code editing** → a model with `insert`. Note it may lack
   `tools`.
3. **Structured extraction** — classification, tagging, field extraction, JSON
   out → prefer a model **without** `thinking` in `capabilities`. See below;
   this is the counterintuitive one and it matters more than size.
4. **Everything else** → the smallest model whose `context_length` comfortably
   fits the input. Bigger is slower and cold-loads worse; on a
   memory-bandwidth-bound host the difference is large. A 4B model is genuinely
   fine for classification.

### The best model for bulk work is usually a small one

Not because small is good enough, but because **the large models on a typical
host all have `thinking` and the small ones often do not** — and for structured
output that capability is a liability, not a feature.

Check `capabilities` before reaching for parameter count. A 4B model with
`completion` and `vision` but no `thinking`, sitting on a six-figure context
window, will return clean JSON while a 26B model wraps the same answer in
reasoning you then have to unpick. Read the roster rather than assuming the
biggest model is the best tool.

**Prefer a model that is already loaded.** `list_running()` shows what is warm.
A cold load of a 20GB model costs real seconds before any tokens appear, so
reusing a warm model often beats picking a marginally better cold one.

### The `thinking` trap

Most modern models declare `thinking` in `capabilities`. These emit reasoning
before their answer — sometimes inline, sometimes in a **separate response
field** that leaves the visible content empty — and **a system prompt saying "no
preamble, JSON only" does not stop it.** That is the model working as designed,
not disobeying.

If you need clean structured output:

- Prefer a model **without** `thinking` for extraction work, or
- Expect the reasoning and parse the answer out of it, or
- Give a **generous** `max_tokens` — thousands, not hundreds.

Do not fight it with increasingly emphatic prompts. It is a property of the
model, not of your wording.

> [!warning] Budget starvation looks exactly like a broken model
> Reasoning is emitted **before** any visible output. Measured 2026-08-26:
> `gemma4:12b` spent ~60 tokens thinking to answer *"reply with only the number
> 7"*. At `max_tokens` of 50, 200 and 600 it returned **empty content with
> `done_reason: length`** — no text at all.
>
> **The signature is `eval_count` equal to the budget, `done_reason: length`,
> and empty content.** That is starvation, not failure. Two working models were
> nearly deleted and re-pulled over this. Raise the budget before concluding a
> model is defective, and check it in the Ollama desktop app if in doubt.

**Prefer `chat` over `generate` for instruction-tuned models.** `generate` sends
a raw prompt without the model's chat template. Template-sensitive families
(Gemma especially) then emit **reserved vocabulary** — repeated `<unused50>` —
instead of text. That output means the template is missing, not that the model
is corrupt.

**A `thinking` model may return its reasoning in a separate field**, leaving the
visible content empty until it finishes. Read both before deciding nothing came
back.

## Local versus cloud

Some models are tagged `:cloud`. They are manifest stubs of a few hundred bytes
that run on Ollama's hosted infrastructure — **content sent to them leaves the
machine.** They are permitted and sometimes correct: they typically offer far
more context than any local model.

The rule is simple: **cloud is a choice, never a default.** Pick one deliberately
when the job needs it, say so in your response so the operator knows, and never pick one
merely because the name sounded capable. If the content is private, this decision
is already made for you.

Every response carries a `location` field. Read it.

## Context limits — the part that bites

**Ollama does not error when input exceeds a model's context. It truncates and
returns a normal-looking response.** A half-read document produces a confident
answer to a question the model only partly saw.

The server refuses over-length input rather than letting this happen, so you
will get a `context_exceeded` verdict with the numbers rather than a quietly wrong result.
**Treat that refusal as the system working**, not as an obstacle to route around
with `allow_truncation`.

### Building a search index — use `vault_index.py`

For semantic search over a folder of markdown, run `vault_index.py` rather than
orchestrating embeddings through tool calls. It ships alongside the server, in
the same directory. **Run the installed copy in place** — never copy it
somewhere convenient first, because a duplicate goes stale silently and the copy
is what you will end up debugging against.

```
python vault_index.py build <folder> --out index.json --rebuild
python vault_index.py search index.json "a question in plain language"
```

Run it with the interpreter from the project's own virtual environment, and note
that it embeds through Ollama, so **whatever runs it needs a network route to the
Ollama host.** A sandboxed or containerised helper usually does not have one:
`localhost` inside a sandbox is the sandbox, not the machine Ollama is on. If a
result comes back without that route existing, something answered from somewhere
else — say so rather than reporting the result.

**Why a script and not a sequence of `embed` calls.** Each vector is hundreds of
floats. Pulling thousands of them into context to do arithmetic wastes enormous
context and hits limits almost immediately. Vectors should be produced, stored
and compared on the machine that holds them; your job is to decide what to index
and to read the *results*, which are small.

It reads the model's real context limit rather than assuming one, chunks on
paragraph boundaries with overlap, and re-embeds only files whose content hash
changed.

#### Reranking — when cosine similarity is not enough

Cosine similarity measures **topical overlap, not answerhood.** A chunk densely
about "GPUs in this machine" scores as well as one explaining why they slowed
down. Chunking cannot fix that; it is the wrong signal for the final ordering.

```
python vault_index.py search index.json "why did the GPUs slow down" --rerank
```

Bare `--rerank` uses `gpt-oss:20b` (~28s, correct on most queries). Add
`--rerank qwen3.6:35b` when only rank 1 will be consumed — more precise, ~102s.
**Do not use `gemma3:4b` as a reranker**: it scores ~45% of candidates as direct
answers regardless of rubric, which is embedding order with confident labels on
it.

#### Reranking cannot repair recall

A reranker only reorders what retrieval hands it. **If the answer is not in the
pool, a better judge changes nothing.**

The tool warns when the pool's cosine spread is narrow (< ~0.05) — that means
the embeddings are barely ranking, so the pool boundary is close to arbitrary,
and that is exactly when the right candidate is most likely to fall outside it.

```
python vault_index.py search index.json "..." --explain "an exact phrase"
```

`--explain` reports where chunks containing that literal text actually rank, and
whether they made the pool. This separates a **recall** failure (widen
`--rerank-pool`, or fix chunking) from a **ranking** failure (change model) —
they need opposite fixes and the normal output cannot tell them apart. Measured
2026-08-26: an answering passage sat at cosine rank 32 against a pool of 20;
every model failed the query, and `--rerank-pool 40` put it at rank 1 unchanged
otherwise.

### Embeddings need chunking

The embedding model's window is small — often *two orders of magnitude* smaller
than the chat models on the same host. Any real document exceeds it.

Chunk before embedding. The refusal message states the character budget; stay
under it. Chunk on paragraph or section boundaries rather than mid-sentence, and
keep enough text per chunk to carry meaning — a chunk of three words embeds to
something useless.

### Embedding models need task prefixes

`nomic-embed-text` is trained with asymmetric prefixes and **degrades silently
without them** — no error, just worse retrieval:

- Documents: `search_document: `
- Queries: `search_query: `

Store the prefixes in the index metadata and take the query prefix **from the
index**, not from current code. An index built under one prefix scheme is not
comparable with a query embedded under another; the arithmetic still works and
returns confident nonsense.

**Never pass `allow_truncation=true` on an embedding you intend to store.** A
truncated embedding is a structurally perfect vector representing only the start
of the document. It will sit in an index looking healthy and quietly degrade
retrieval for months. This is the single worst failure available here.

## Reading verdicts

Every response carries a `verdict`. Only one means "use this".

| Verdict | Meaning |
|---|---|
| `ok` | Complete. Safe to use |
| `truncated` | Hit the token limit — output is **incomplete**. Do not parse as whole JSON. Raise `max_tokens` and retry |
| `completion_unverified` | Ollama did not report how it ended. Cannot be confirmed complete — treat as possibly truncated |
| `context_exceeded` | Input too large. Chunk it or pick a bigger model |
| `context_unknown` | The limit could not be read, so nothing was checked |
| `empty_generation` | Text came back empty |
| `model_not_found` | Not installed — **and if it was there before, it has been deleted.** Worth reporting to the operator |
| `ollama_unreachable` | The server is not running or the host is down |

The distinction between `ok` and everything else matters more than it looks.
Several of these return *usable-looking text* alongside a warning verdict. Reading
only the text is how a partial result gets treated as a finished one.

## Delegating well

**Specify the output shape precisely.** Small models follow a stated format far
better than they invent a sensible one. Give the exact JSON keys or the exact
line format you want.

**Use `temperature: 0` for anything mechanical.** Classification, extraction and
normalisation should be deterministic. Save higher temperatures for prose.

**Batch small — batch size is a correctness parameter, not a throughput knob.**
Many items per call amortises the round trip, but a small model loses track of
which item is which long before it hits the context limit. Measured 2026-08-26:
twenty passages in one ~8,300-token call produced perfectly-formed scores that
did not correspond to the passages; batches of **five** fixed it with the same
model, prompt and total token volume. Usable reasoning context was under 2,000
against a nominal 131,072.

**Nominal context is what a model accepts, not what it can reason over.** For
anything needing item-level tracking, find the working limit empirically.

**Keep the prompt terse.** Prompt complexity trades against format compliance at
small sizes. An elaborated, more careful rubric made `gemma3:4b` abandon the
requested output shape entirely and return a ranking instead of scores, losing
7 of 20 items to a parse failure. The blunt version worked.

**Spot-check the output before trusting the batch.** Read a handful of results
against the source. Local models fail differently from large ones — they tend to
drift into a plausible but wrong pattern partway through rather than producing
obvious nonsense, so an eyeball on items 1, 50 and 200 catches more than
inspecting the first few.

> [!warning] Format compliance is not a quality signal
> **A small model will comply with your output format long after it has stopped
> understanding the task.** Measured 2026-08-26: `gemma3:4b` returned twenty
> perfectly formatted scores in 1.5s that rated a NAS reset-button note a direct
> answer to a GPU question. Parse success, sensible token count, complete
> coverage — every health signal green, the content noise.
>
> Validate against **known-good content** and against the **cheaper baseline the
> model has to beat**. "It parsed" and "it ran" are not evidence of correctness,
> and this failure mode looks healthier than a real one.

**Report the distribution, not just the count, when a model assigns scores.** A
model that gives most items the same score has ranked nothing, while reporting
complete success — the ties then fall through to whatever tiebreak sits behind
it. `scored 20 of 20` measures participation; the spread measures discrimination.

**Say what you delegated.** In your response, name the model and whether it ran
locally or in the cloud. Nobody should ever have to ask where their content
went.

## When it fails

- `ollama_unreachable` — the server may not be running, or you may be pointed at
  a host that is powered off. Report it plainly; do not silently fall back to
  doing the whole job inline without saying so.
- A model that used to exist returning `model_not_found` means it was deleted.
  Say so rather than quietly switching models — the operator may not know, and a
  silent substitution changes what their results were produced by.
- If results are poor, the usual causes in order: wrong model for the job, output
  shape underspecified, temperature too high, or a `thinking` model being asked
  for clean JSON.

## Recording what you learn

**Nothing here asks you to maintain a file.** What models exist is a question
with a live answer — ask `list_models`, never a document. A written roster is
wrong as soon as anyone pulls or deletes a model, and keeping one in sync is
work the host is already doing for free.

**What is worth surfacing is what a measurement cost you to learn** and cannot be
read back off the host: a model's working batch size, whether it holds a format
under load, where its usable reasoning context actually ends as opposed to what
it nominally accepts. None of that appears in `list_models`.

**Report it in your response, plainly, with the number attached.** *"Batches of
20 scrambled the item ordering; 5 was clean"* is worth more to the operator than
a score. If they keep notes of their own they will file it; if they do not, they
have still been told. **What you must not do is quietly absorb the finding and
carry on** — the next session starts without it.
