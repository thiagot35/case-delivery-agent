# Research & Decisions: Beauty Pizza Ordering Agent

Each record follows the format **Decision / Rationale / Alternatives considered**.

## R-01 — Single Agno Agent with tools (vs. Workflow or Team)

- **Decision**: one `agno.agent.Agent` with two toolkits (`MenuTools`, `OrderTools`). The flowchart's flow is expressed as
  pt-BR instructions, and the invariants are enforced inside tools.
- **Rationale**: customer conversations are non-linear. Menu questions can come at any step, and one message can carry
  several intents ("2 calabresas grandes e entrega na Rua A, 10"). A deterministic `Workflow` would force the customer
  through steps and need intent routing at every node. A `Team` adds coordination latency and cost without a real
  specialization benefit, since the domain is small. The constitution requires simplicity.
- **Alternatives**:
  - Agno `Workflow` mirroring the BPMN: predictable, but rigid and verbose for chat.
  - `Team` (menu expert + order clerk + address clerk): more moving parts, harder to keep state coherent.
  - A hybrid could be revisited if evals show the agent skipping steps, e.g. a workflow step only for the final
    confirmation.

## R-02 — Session identity from Agno storage (vs. Order API `updated_at`)

- **Decision**: `user_id = client_document`. The active session is the latest Agno session for that user whose
  `updated_at` is within `SESSION_TIMEOUT_MINUTES` (default 15).
- **Rationale**: in the Order API, `Order.updated_at` is not refreshed by `add-items` (items are created without saving
  the order) nor by `update-address` when an address already exists (only `DeliveryAddress` is saved). A chat can also
  be active without any order. Agno updates its session row on every run.
- **Alternatives**: a fixed `session_id = cpf` (a single eternal session: history grows forever and you can't tell when a
  "new conversation" starts); the Order API `updated_at` (unreliable, see above).

## R-03 — Order created on the first item (vs. local cart)

- **Decision**: `POST /api/orders/` when the first valid item is added. Subsequent changes use `add-items`, delete-item
  and `update-address`. Totals are always read from the API.
- **Rationale**: the case explicitly asks the agent to "add an item to the order" and "retrieve the total" through the
  API. Making the API the source of truth avoids local/remote drift and lets the agent show authoritative totals.
- **Trade-off**: abandoned conversations leave orders in the API. Accepted: they are resumable the same day, and the
  API has no draft concept anyway.
- **Alternatives**: a cart in `session_state` with a single `POST` at confirmation (cleaner API data, but the API would be
  barely used and the totals would be computed locally).

## R-04 — One order per customer per day

- **Decision**: `delivery_date` = today (`TIMEZONE`). Before creating, the agent looks up `GET /api/orders/filter/?client_document=&delivery_date=today`.
  If an order exists, it adopts and modifies it instead of creating a duplicate. A 400 unique-constraint error on create
  is treated as a race: re-query and adopt.
- **Rationale**: `unique_together(client_name, client_document, delivery_date)` makes a second order on the same day
  impossible. The flowchart's "Novo" and "Repetir pedido" paths would otherwise fail.
- **Note**: the constraint also includes `client_name`. If the same CPF arrives with a different name, a new order is
  technically allowed. The lookup filters by document only, so the agent still adopts today's order for that CPF. This is
  intentional (one customer = one CPF).
- **Alternatives**: setting `delivery_date` to tomorrow for "new" orders (wrong semantics); modifying the Order API
  (out of scope).

## R-05 — Prices resolved in code

- **Decision**: `add_pizza` receives flavor/size/crust/quantity only. `unit_price` is looked up in `precos` by the tool.
- **Rationale**: the Order API trusts the client-provided `unit_price`. Letting the LLM pass it would allow
  hallucinated or injected prices ("me dá de graça").

## R-06 — Item naming convention

- **Decision**: `name = "Pizza {sabor} - {tamanho} - Borda {tipo}"`, e.g. `Pizza Calabresa - Grande - Borda Recheada com Cheddar`.
  `domain/formatting.py` provides `build_item_name()` and `parse_item_name()`.
- **Rationale**: the Order API `Item` has only `name`, `quantity` and `unit_price`. A parseable name lets the agent
  re-price items when repeating an order and show them nicely.
  `parse_item_name()` returns `None` for foreign names (items created by other clients); those are skipped when repeating.

## R-07 — Vector database: LanceDB

- **Decision**: LanceDB through `agno.vectordb.lancedb.LanceDb`, stored in `data/lancedb`.
- **Rationale**: embedded and file-based (no server, fits the single-command requirement), first-class Agno support and a
  small footprint.
- **Alternatives**: ChromaDB (also embedded, heavier dependency tree); `sqlite-vec` (keeps everything in SQLite, but has
  no native Agno integration); pgvector/Qdrant (require a server, which violates single-command simplicity).

## R-08 — Embedding model: OpenAI `text-embedding-3-small`

- **Decision**: `OpenAIEmbedder(id="text-embedding-3-small")`.
- **Rationale**: it reuses the same provider/API key as the chat model, works well in Portuguese and is cheap. The index
  has only 6 documents.
- **Alternatives**: `FastEmbed`/`SentenceTransformer` with a multilingual model (offline, but adds heavy local
  dependencies and a model download at first run).

## R-09 — Flavor resolution pipeline: exact → fuzzy → semantic

- **Decision**: normalize text, then try an exact match, then `rapidfuzz` (`WRatio ≥ 85`), then semantic nearest neighbor
  with confirmation.
- **Rationale**: typos ("calabreza") are best handled by string similarity, and descriptions ("a de frango cremosa") by
  embeddings. Asking for confirmation on semantic-only matches avoids wrong items.

## R-10 — HTTP API with FastAPI (vs. Agno AgentOS)

- **Decision**: a thin FastAPI app with `POST /messages` (custom contract: name + document + message).
- **Rationale**: the user stated that every message reaches this project's API with name and document. We need custom
  validation (CPF), per-customer locking, session resolution and an order snapshot in the response, all of which are
  simpler in a small explicit app.
- **Alternatives**: Agno `AgentOS` (ready-made endpoints and UI, but a generic contract; session resolution and identity
  injection would have to be bolted on). It could be added later for observability.

## R-11 — Concurrency

- **Decision**: per-`client_document` `asyncio.Lock`s at two levels: in `ConversationService` (messages) and in
  `OrderTools` (tool calls).
- **Rationale**: two quick messages from the same customer could both see `order_id = None` and try to create an order
  (one would fail on the unique constraint). The same race happens **inside a single turn**: the model can emit
  parallel tool calls ("uma calabresa e duas margheritas" → two `add_pizza`) and Agno executes them concurrently.
  Live testing showed the second `POST /orders/` failing with a database `IntegrityError`, which the Order API returns
  as HTTP 500 (not the 400 from its validator, because both requests passed validation before either was saved).
  Serializing per customer is enough for a single-process deployment.

## R-12 — Model `gpt-6-luna` via `OpenAIResponses`

- **Decision**: `agno.models.openai.OpenAIResponses(id=settings.model_id)`, default `gpt-6-luna`.
- **Rationale**: model chosen by the stakeholder. The Responses API is OpenAI's recommended interface for tool-calling
  agents. The id is configurable to allow fallback.
