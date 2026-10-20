## Inspiration

Plans rarely fail in big ways. They fail in small ones: a train running 45 minutes late, a meeting dropped on top of
another, rain on an outdoor dinner, an office that is closed on a public holiday, two people wanting the same TV at
8pm. Each problem is easy to fix on its own. The hard part is noticing it in time, and knowing that the fix doesn't
break something else.

Voice assistants are great at understanding "my boss just moved our meeting to 1pm". But a language model on its own
can't *guarantee* that a new plan works, and it will sometimes say it does when it doesn't. I wanted an Alexa+
experience I could trust with my day: one that understands me like an LLM, but checks plans like a strict engine,
and never changes anything without asking.

## What it does

**Situation Guard for Alexa+** keeps your plans working. You tell Alexa what you're doing. When something clashes or
changes, she finds fixes, a deterministic engine tests every one, and nothing changes until you say yes.

A real conversation:

1. *"What's the temperature today?"* Live weather where you are.
2. *"Schedule a meeting with my boss tomorrow at 2pm for an hour."* It's checked for clashes, then added to your
   real Google Calendar.
3. *"Tomorrow I also want to talk with a teammate at 2pm."* *"You already have the meeting with your boss at 2pm.
   Shall I move the call to 3pm?"*
4. *"At 2pm I want to go from here to Sydney Airport by public transport."* Alexa searches real Sydney train routes
   with Amazon Location, and the engine rejects every one that overlaps your meeting.
5. When you confirm a trip, she checks the weather for when you leave: *"Rain likely at 5pm. Take an umbrella."*

It is **generic**, not a travel app. The same engine handles:

- meetings and double bookings
- a shared TV (one programme at a time)
- outdoor plans and rain
- offices closed on public holidays
- deadlines and steps that depend on each other ("Dad's flight needs him at the airport first")

The idea in one line: **Alexa proposes, the engine decides, you approve.**

- **Alexa proposes:** she turns speech into plan edits and decides which alternatives to try.
- **The engine decides:** only `try_option` can say an option works. Alexa may never claim a fix works unless the
  engine returned `feasible: true`.
- **You approve:** `confirm_option` is the only way to switch a plan to a new option. It is blocked in code unless your
  last message was a clear yes. After saving, the plan is reloaded and re-checked to verify the change really happened.

## How I built it

I built it on my own, in Python, in two parts that judges can try.

**1. A self-hosted MCP server** (MCP Python SDK, Streamable HTTP, protocol `2025-11-25`) with **17 tools**:

- **Reading:** list_situations, get_situation
- **Changes:** report_change
- **Options:** find_options, try_option, confirm_option
- **Planning:** create_situation, add_place, add_resource, add_commitment, add_goal, add_dependency
- **Outside world:** get_weather, check_weather, check_public_holidays, import_calendar, add_to_calendar

Every tool returns structured results (Pydantic models) carrying the engine's verdict and its reasons. Errors are
written to help an agent recover, for example "No option 'E'. Current options: A, B, C, D".

**2. A simulated Alexa+ web app** (FastAPI with plain HTML, CSS and JavaScript):

- voice in and voice out (Web Speech API)
- your device location
- a plan timeline
- an **Activity** panel showing every MCP tool call with the engine's verdict

The web app reaches the tools through the same `/mcp` HTTP endpoint any MCP client would use, not by calling Python
directly.

**Under the hood:**

- **The feasibility engine.** Deterministic rules:
  - dependencies and deadlines
  - shared resources (capacity)
  - one person can't be in two places at once
  - nothing can be scheduled in the past
  - places can be unavailable (rain, holidays)
  - goals at risk

  Each fix is a list of edits that is simulated on a copy of the plan before anyone sees it.
- **Option sources:**
  - **Amazon Location Service Routes** (`CalculateRoutes`, transit and car), for real journey alternatives
  - a generic **reschedule** source, which moves a clashing thing before or after, or moves the other one
- **Outside world:**
  - **Amazon Location Places** (`SearchText`), for addresses and landmarks
  - **Google Calendar API** (read, add, and move after approval)
  - **Open-Meteo** for weather
  - **Nager.Date** for public holidays
  - the browser's location
- **Alexa's brain.** An agent loop that calls the MCP tools. It runs on OpenAI `gpt-5-mini`, and an **Amazon Bedrock**
  (Amazon Nova 2 Lite) provider is built in and is one setting away.
- **Least privilege.** The AWS user may only search routes and places, and Google gets only the `calendar.events`
  permission. Keys live in `.env`, and Google credentials in a gitignored `secrets/` folder.
- **213 tests that run offline and free.** They use recorded real API responses and fakes for AWS, Google and the
  LLM, and drive every tool through the real MCP protocol.

## Challenges I ran into

- **Making an LLM trustworthy.** Early on, the model would happily say "option B works" when it didn't. I solved it
  with structure, not prompting alone:
  - only the engine's `try_option` verdict counts
  - `confirm_option` is blocked in code without a real "yes"
  - every confirmed change is reloaded and re-verified
- **Keeping it generic.** It started as "my train is late". I restructured it so travel is just one option source,
  and the same engine handles meetings, a TV, weather and holidays.
- **Amazon Bedrock access.** My account was blocked with `Operation not allowed` even though the IAM policy allowed the
  call. I built the Bedrock provider anyway and used OpenAI for the demo.
- **Real-world details:**
  - time zones and offsets everywhere
  - a train that already left is not an option
  - Amazon Location Places and Routes are separate IAM permissions
  - the weather service's geocoder rejects "Sydney, NSW"
- **A sneaky configuration bug.** The MCP server chose its data sources before `.env` was loaded, so "live" weather
  silently fell back to recorded data. The UI said live while the tools weren't. Now settings load first.
- **Voice that listens properly.** The browser's speech recognition stopped at the first pause, so "What's… the
  weather today" became "What's". I switched to continuous recognition with a silence timer.
- **Google sign-in setup.** The OAuth consent screen wizard has several steps, and test tokens expire after 7 days.

## Accomplishments that I'm proud of

- **Fixes are verified before they are offered.** Alexa only says "this works" when a deterministic engine agrees,
  and only changes your plan (and your calendar) after you say yes.
- **Real data end to end:** real Sydney transit routes from Amazon Location, your real Google Calendar, live weather,
  real public holidays.
- **One engine for many situations,** from the shared TV to the airport run, with no special cases for travel.
- **It runs anywhere.** Every source has a free offline mode with recorded real responses, so anyone can clone the
  repo and try the demo with no keys at all.
- **A transparent UI.** The Activity panel shows each tool call, the alternatives found, why each was rejected, and
  the final verified change.

## What I learned

- **Split understanding from deciding.** LLMs are excellent at turning speech into structured edits and choosing
  what to try. Deterministic code should decide what is true. MCP is a natural boundary between the two.
- **Design tools for agents.** Clear names, structured results and helpful error messages matter more than clever
  prompts. A good refusal message lets the model fix its own call.
- **Consent belongs in code.** "Ask before changing things" works much better as an enforced rule than as a sentence
  in a prompt.
- **Least privilege from day one** makes live AWS and Google integrations much less scary to turn on.

## What's next for Situation Guard for Alexa+

- **Real Alexa+ devices.** Connect through account linking and MCP authorization, so each request acts for the right
  person with their own calendar and plans.
- **Many users.** Per-user Google sign-in (web OAuth), per-user plans in DynamoDB with encrypted tokens, and Google
  app verification.
- **Amazon Nova as Alexa's brain** on Bedrock, once account access is enabled. The provider is already built.
- **More of the outside world:** live train delays (Transport for NSW), Outlook calendars, traffic incidents.
- **Watching plans proactively.** Spot a problem before you ask ("your 5pm train is cancelled; option C still gets you
  there") and offer the fix.
