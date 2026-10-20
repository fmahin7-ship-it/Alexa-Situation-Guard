# Product feedback

Feedback on every tool, API and SDK used to build Situation Guard, written from what actually happened while
building it. Each section covers what I used it for, what worked, what needs work, how onboarding went, and
whether I would build with it again.

---

## Model Context Protocol: Python SDK (`mcp` 2.3.0)

**Used for:** the Situation Guard MCP server: 17 tools over Streamable HTTP (protocol `2025-11-25`), mounted inside
a FastAPI app at `/mcp`, plus the MCP client the simulated Alexa+ app uses to reach those tools.

**What worked**
- Returning Pydantic models from tools gave me structured output and output schemas for free. The agent receives
  typed JSON with the engine's verdict, not text to parse.
- `ToolError` turns a refusal ("No option 'E'. Current options: A, B, C, D") into a clean tool error that the model
  can read and recover from.
- Tool annotations (`destructive_hint`, `idempotent_hint`) let me mark `confirm_option` as the one dangerous tool.
- The in-memory `Client(server)` made the test suite fast and fully offline: every tool is tested through the real
  MCP protocol without a network.
- Mounting the Streamable HTTP app into an existing FastAPI app was a few lines.

**What needs work**
- The Streamable HTTP session manager can only be started once per process. Running the app's lifespan in more than
  one test fails, so web tests had to skip the lifespan. A reusable or resettable manager would help testing.
- Choosing the client mode that speaks protocol `2025-11-25` (`mode="legacy"`) was not obvious from the name. I
  found it by reading the source.
- Some generated JSON schemas (`anyOf: [X, null]`, `title` everywhere, `default: null`) are rejected or misread by
  some model providers. I had to simplify schemas before sending them to Amazon Bedrock. An option to emit
  "provider-friendly" schemas would save every SDK user this step.

**Onboarding:** Good. The server side took under an hour. Testing patterns took longer to discover.
**Would build with it again:** Yes. It is the backbone of the project.

---

## Amazon Location Service: Routes (`geo-routes` `CalculateRoutes`)

**Used for:** real alternatives when a journey breaks, such as "the train is 45 minutes late, what else gets Dad to
his flight?". The app uses public transport and car routes, depart-at and arrive-by searches, and several alternatives per
search.

**What worked**
- Transit routing returned real Sydney train services with line names and changes (for example T8 direct, or T4
  with a change at Wolli Creek), with exact departure and arrival times. This made the demo feel real.
- One request returns several alternatives, which is exactly what a "find options, let the engine test them"
  design needs.
- Cheap per request, and a least-privilege IAM policy (`geo-routes:CalculateRoutes` on the provider resource)
  was enough.

**What needs work**
- The IAM resource ARN (`arn:aws:geo-routes:<region>::provider/default`) is not where you would guess, and the
  console does not offer it. A ready-made least-privilege policy in the docs would save time.
- Transit support needs a recent boto3. An older install (on Python 3.9) silently lacked the Transit travel mode,
  which was confusing until I upgraded.
- The response is deep and heavily nested. A short "here is how to get departure, arrival and line names for a
  transit route" example would help a lot.

**Onboarding:** Medium. The API is powerful, but getting from "enable the service" to "first transit route" took
some trial and error.
**Would build with it again:** Yes.

---

## Amazon Location Service: Places (`geo-places` `SearchText`)

**Used for:** turning spoken places ("Sydney Airport", a street address, "home") into coordinates for travel and
weather.

**What worked**
- A single text search with a country filter and a bias point ("near the other places in this plan") gives good
  results.
- The `IntendedUse=Storage` option clearly states whether results may be stored. The app stores places in saved plans,
  so this mattered.

**What needs work**
- With only route permissions, the first call failed with `AccessDeniedException` on
  `geo-places:SearchText`. The error message is clear, but Places and Routes being separate services with separate
  IAM actions is easy to miss when you think of "Amazon Location" as one product.

**Onboarding:** Easy once the IAM action was known.
**Would build with it again:** Yes.

---

## Amazon Bedrock (Converse API, Amazon Nova 2 Lite)

**Used for:** a second "brain" for Alexa next to OpenAI. The provider is fully written: tool use via the Converse
API, and one attempt per call so a failure is reported instead of silently retried and billed.

**What worked**
- The Converse API's single request and response shape for messages and tool use is easy to map to my own agent
  loop.
- A permission problem (`AccessDeniedException`) and an account problem are distinguishable, which helped
  diagnosis.

**What needs work**
- My account could not use Bedrock at all. Calls to Amazon Nova 2 Lite returned
  `ValidationException: Operation not allowed` even though the IAM policy allowed the call. As far as I could tell,
  the new account was still restricted. Nothing in the console said so, and the error does not explain why or what
  to do. A clear "your account is not yet enabled for Bedrock: here is how to fix it" message would have saved hours.
- Requesting access to Anthropic models needed a use-case form. It errored for me, and its country and billing
  fields did not fit a developer based outside the listed countries.
- Nova rejected some JSON schemas that other providers accept (see the MCP section), so I had to add schema
  simplification.

**Onboarding:** Hard, blocked by account status rather than by code. I switched Alexa to OpenAI for the demo, and
Bedrock stays one setting away (`SITUATION_GUARD_LLM=bedrock`).
**Would build with it again:** Yes, once account access is sorted. I would like Alexa's brain to run on Amazon Nova.

---

## OpenAI API (`gpt-5-mini`, Chat Completions with tools)

**Used for:** Alexa's brain in the demo: understanding requests, calling the MCP tools, and replying in short,
spoken-style sentences.

**What worked**
- Reliable multi-step tool calling: create a plan, add places and commitments, find options, try them, then ask.
- `reasoning_effort=low` kept latency and cost reasonable for a voice assistant.
- Turning off automatic retries (`max_retries=0`) made failures visible instead of being billed twice.

**What needs work**
- Without firm instructions the model over-asks ("Yes or no?" after almost every message) and once offered
  something no tool could do (a "typical seasonal summary" of the weather). I added explicit prompt rules, and a
  code-level consent check so the plan can only change after a real "yes".
- `gpt-5-mini` has a published shutdown date (11 Dec 2026), so anything built on it needs a planned migration.

**Onboarding:** Easy.
**Would build with it again:** Yes, alongside an AWS-hosted model.

---

## Google Calendar API + Google OAuth (`google-auth`, `google-auth-oauthlib`)

**Used for:** reading the user's real meetings into the plan, adding a meeting when the user asks to schedule one
(only if the engine finds no clash), and moving an event after the user approves a fix.

**What worked**
- The `calendar.events` scope is narrow: events only, nothing else in the account.
- `singleEvents=true` expands repeating meetings into real occurrences, which is exactly what a clash checker needs.
- The desktop OAuth flow (`InstalledAppFlow`) is a one-command sign-in for a single user, and token refresh is
  automatic.

**What needs work**
- The Google Auth Platform setup (branding, audience, contact, policy agreement, test users, client) is a
  multi-step wizard. Once, the configuration did not save and the page went back to "not configured yet", with no
  error shown.
- In testing mode, refresh tokens expire after 7 days, which is easy to forget during a hackathon.
- Going from one user to many means switching to the web OAuth flow, storing a token per user, and passing Google's
  app verification for a sensitive scope. That is a big jump from the single-user quick start.

**Onboarding:** Medium. The code was quick; the console setup was the slow part.
**Would build with it again:** Yes.

---

## Open-Meteo (weather)

**Used for:** "what's the weather today", rain at outdoor places (which makes them unavailable and breaks plans
there), and "take an umbrella" advice when leaving for a trip.

**What worked**
- Free, no key, and fast. Current conditions, a daily forecast and hourly rain probability come back in one request.
- Hourly rain probability is exactly the signal a planner needs.

**What needs work**
- The geocoder only matches place names. "Sydney, NSW" returns nothing until you drop everything after the comma.
- Free use is non-commercial only and needs CC BY 4.0 attribution. Fine for a hackathon, but a product needs the
  commercial plan.

**Onboarding:** Excellent: first forecast in minutes.
**Would build with it again:** Yes.

---

## Nager.Date (public holidays)

**Used for:** marking places that close on public holidays (such as an office) as unavailable on those days.

**What worked:** A simple yearly endpoint per country, with state-level holidays (`AU-NSW`). It was enough to catch
"your quarterly review is on Christmas Day and the office is closed".

**What needs work:** Nothing significant for my use. Commercial use terms need checking for a product.

**Onboarding:** Excellent.
**Would build with it again:** Yes.

---

## Browser Web Speech API (speech recognition and speech synthesis)

**Used for:** speaking to Alexa and hearing her replies in the simulated Alexa+ web app.

**What worked:** No dependencies, decent accuracy in Chrome and Edge, and speech synthesis that is good enough for
a demo.

**What needs work**
- By default, recognition ends at the first short pause. Users saying "What's… the weather today" had only "What's"
  sent. I switched to continuous recognition with interim results and my own 1.5-second silence timer.
- Recognition is only available in Chrome and Edge.

**Onboarding:** Easy, but the defaults suit commands better than conversation.
**Would build with it again:** Yes, for prototypes.

---

## FastAPI, Uvicorn and Pydantic

**Used for:** the web app's API, serving the page, and hosting the MCP server. Pydantic also models every plan,
edit and tool result.

**What worked:** Pydantic models double as the engine's data model, the saved-state format and the MCP tool output
schemas: one definition everywhere. FastAPI's test client made the web API easy to test.

**What needs work:** Browsers cached my JavaScript between redesigns, so an old script ran against new HTML and
buttons silently stopped working. I now add a version to static file links. A built-in option for cache-busting
static files would help.

**Onboarding:** Excellent.
**Would build with it again:** Yes.

---

## The Alexa+ track (simulated path)

**Used for:** the simulated Alexa+ web app that talks to my MCP server exactly as an MCP client would.

**What worked:** Allowing a simulated Alexa+ path let me build and show a full multi-turn, multi-tool agent
experience without waiting for device access.

**What needs work / feature requests**
- A hosted way to point a real Alexa+ (or an Alexa+ simulator) at a self-hosted MCP server would let builders test
  the real voice experience end to end.
- Guidance on account linking and per-user authorization for MCP servers used by Alexa+ (whose calendar, whose
  plans) would help projects plan for production from day one.
