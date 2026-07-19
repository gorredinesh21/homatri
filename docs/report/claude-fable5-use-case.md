# Homaatri — Anthropic Claude (Fable 5) Use-Case Details

Copy-paste answers for the Anthropic "Submit use case details" form when enabling
Claude on Amazon Bedrock (`anthropic.claude-fable-5`).

## Claude Fable 5 — quick facts

| | |
|---|---|
| **Bedrock model ID** | `anthropic.claude-fable-5` (the `anthropic.` prefix is required on Bedrock) |
| **Positioning** | Anthropic's most capable widely released model — demanding reasoning & long-horizon agentic work |
| **Context / output** | 1M-token context window (default), up to 128K output tokens |
| **Pricing** | $10 / 1M input · $50 / 1M output (above Opus-tier; ~10× Haiku output) |
| **Data retention** | Requires 30-day retention (no zero-data-retention); on Bedrock governed by the platform's retention settings |

> **Cost note:** Fable 5 is the top of the range. Homaatri's routine work (order parsing,
> menu matching, coordination, tool calls) sits within Sonnet/Haiku capability. Sensible
> pattern: **Fable 5 primary for the hard turns, a cheaper model as fallback for routine
> turns** — the provider-swap design already supports this.

## Primary use-case description (form free-text box)

> Homaatri is a WhatsApp-first, hyper-local food-ordering platform operating in India that
> connects three parties in a single conversation: customers, home-based cooks ("chefs"), and
> local delivery riders. We use **Anthropic Claude Fable 5** (via Amazon Bedrock) as the
> conversational and tool-calling engine that runs the entire order lifecycle — order taking,
> cross-role coordination, and support.
>
> We chose Fable 5, Anthropic's most capable model, because the workload is harder than a
> typical chatbot in three specific ways:
>
> 1. **Ambiguous, code-mixed natural language.** Customers order in colloquial, typo-heavy,
>    mixed Hindi-English ("2 paaneer batter musala, kam teekha") that must be parsed reliably
>    into a structured order against a live menu. Fable 5's stronger reasoning reduces
>    mis-parsed orders — which, in a real transactional flow, mean wrong food and lost money.
> 2. **Long-horizon, multi-party agentic coordination.** A single order runs a stateful
>    lifecycle (placed → paid → cooking → modified → ready → dispatched → delivered) across
>    three roles, with mid-flight changes (add an item, change delivery time or address).
>    Fable 5 uses tool-calling to read and update order state in our PostgreSQL database and
>    maintains shared context across the customer/chef/rider trio, where correctness over a
>    long, evolving interaction matters.
> 3. **Reliable tool use in a money-handling flow.** Because the assistant's tool calls create
>    orders, apply modifications, and trigger dispatch and payment, we prioritize a model whose
>    function-calling and state reasoning are dependable.
>
> Claude's responses are delivered to end-users over WhatsApp, so outputs are seen by people
> outside our company. Deployment is in India; we are in a low-volume pilot (a few thousand
> messages/month). Usage is strictly limited to food-ordering and delivery-coordination
> conversations and complies fully with the AWS Acceptable Use Policy — no prohibited use cases.

## Short version (small box)

> Conversational + tool-calling assistant for a WhatsApp-based hyper-local food-ordering
> service in India. We use Claude Fable 5 (Anthropic's most capable model, via Amazon Bedrock)
> because the task needs reliable parsing of ambiguous, code-mixed orders and dependable
> long-horizon coordination across customer, home cook, and rider — with tool calls that update
> order state in our database and trigger payment and dispatch. Replies go to customers over
> WhatsApp. Low-volume pilot; complies with the AWS Acceptable Use Policy.

## Surrounding form fields

| Field | Answer |
|---|---|
| Company / org | Homaatri |
| Website | Your domain, or `https://github.com/gorredinesh21/homatri` for the pilot |
| Industry | Food & Beverage / Food delivery (closest option, e.g. "Retail & eCommerce") |
| Intended users | Both internal (chefs & riders) and external (customers) |
| Responses seen outside your company? | **Yes** — the assistant replies to customers over WhatsApp |
| Country / region | India |
| Expected volume | Low — pilot (a few thousand messages/month) |
