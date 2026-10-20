# Friction log

Problems I hit while building Situation Guard for Alexa+, in the format the hackathon asks for:
task, steps, expected vs actual, severity, workaround, and suggestion.

| # | Area | Severity |
|---|---|---|
| 1 | Amazon Bedrock: account not enabled, unclear error | High |
| 2 | Amazon Bedrock: Anthropic model-access form | Medium |
| 3 | boto3 on Python 3.9 cannot request Transit routes | Medium |
| 4 | Amazon Location replies fail the service's own model in tests | Low |
| 5 | Amazon Location Places needs a separate IAM action | Low |
| 6 | MCP tool schemas rejected or misread by Amazon Nova | Medium |
| 7 | MCP Python SDK: Streamable HTTP session manager runs once per process | Low |
| 8 | Google Calendar OAuth setup did not save; test tokens expire | Medium |
| 9 | Browser speech recognition stops at the first pause | Medium |

---

## 1. Amazon Bedrock refuses every call with "Operation not allowed"

- **Task:** Use Amazon Bedrock (Converse API, Amazon Nova 2 Lite) as Alexa's brain.
- **Steps:** Created a dedicated IAM user with `bedrock:InvokeModel` for the model. Called `Converse` with the global
  inference profile `global.amazon.nova-2-lite-v1:0` in `ap-southeast-2`.
- **Expected:** A model reply, or a clear permissions error if something was missing.
- **Actual:** `ValidationException: Operation not allowed`. The IAM policy did allow the call (a different model
  returned a normal `AccessDeniedException`, proving IAM was being checked). As far as I can tell the new account
  was simply not enabled for Bedrock yet. Nothing in the console said so.
- **Severity:** High. It blocked the AWS-native brain for the whole hackathon.
- **Workaround:** Built and unit-tested the Bedrock provider anyway (`app/agent/providers/bedrock_converse.py`) and
  ran the demo on OpenAI. Switching back is one setting: `SITUATION_GUARD_LLM=bedrock`.
- **Suggestion:** Return an explicit error such as "This account is not yet enabled for Amazon Bedrock", with a link
  to how to request access, and show the account's Bedrock status in the console.

## 2. Anthropic model-access form on Bedrock would not submit

- **Task:** Request access to Anthropic models on Amazon Bedrock.
- **Steps:** Opened model access, filled in the use-case form.
- **Expected:** Submit and wait for approval.
- **Actual:** The form errored. Its country and billing fields did not fit my situation (a developer whose billing
  details are in a different country from where I build).
- **Severity:** Medium.
- **Workaround:** Targeted Amazon Nova instead, and used OpenAI for the demo.
- **Suggestion:** Clearer validation messages on the form, and a list of supported countries and billing set-ups
  before the user starts.

## 3. Latest boto3 for Python 3.9 cannot request Transit routes

- **Task:** Call `CalculateRoutes` with `TravelMode="Transit"` from Python.
- **Steps:** `pip install boto3` on Python 3.9.13, which gave boto3 1.42.97. Checked the `geo-routes` service model.
- **Expected:** The same travel modes the AWS CLI accepts (the CLI accepts `Transit`, and my recordings were made with it).
- **Actual:** `RouteTravelMode` has no `Transit` in that boto3. It is available in boto3 1.43.109 on Python 3.11.
- **Severity:** Medium. A silent capability gap with no error until you call it.
- **Workaround:** Moved the project to Python 3.11 (`uv venv --python 3.11`).
- **Suggestion:** Say in the Amazon Location docs which SDK versions support Transit routing. A clear error
  ("Transit requires botocore ≥ X") would help more than a generic validation failure.

## 4. Amazon Location `CalculateRoutes` replies omit members the service model marks required

- **Task:** Unit-test my Amazon Location provider offline with botocore's `Stubber`, replaying a real recorded
  `geo-routes calculate-routes` response.
- **Steps:** Recorded a real Transit response (Central Station to Sydney Airport, `--max-alternatives 2`) with the
  AWS CLI. Passed it to `Stubber.add_response("calculate_routes", ...)`.
- **Expected:** A real response from the service validates against the service's own model.
- **Actual:** `ParamValidationError`: missing required `Routes[].MajorRoadLabels`,
  `PedestrianLegDetails.AfterTravelSteps`, `PedestrianLegDetails.Notices`, and `TurnStepDetails.Intersection`. The
  real service leaves out these members when they are empty, but the model marks them required.
- **Severity:** Low. Testing only.
- **Workaround:** Fill the missing members with empty lists before stubbing (`tests/test_travel_search.py::_as_stub_reply`).
- **Suggestion:** Either always return the required members (as empty lists), or mark them optional in the model so
  recorded real responses can be replayed in tests.

## 5. Amazon Location Places needs its own IAM action

- **Task:** Turn spoken places ("Melbourne Airport", a street) into coordinates with Places `SearchText`.
- **Steps:** Turned on live place search with the same IAM policy I used for routes (`geo-routes:CalculateRoutes`).
- **Expected:** Places found, since both are "Amazon Location".
- **Actual:** `AccessDeniedException` for `geo-places:SearchText` on `arn:aws:geo-places:ap-southeast-2::provider/default`.
  The message was clear, but Routes and Places being separate services with separate actions and resource ARNs was
  easy to miss.
- **Severity:** Low.
- **Workaround:** Added the second action to the policy. The exact least-privilege policy is in the README.
- **Suggestion:** Show a ready-made least-privilege policy covering Routes and Places together in the getting-started
  docs, including the `provider/default` resource ARNs, which the console does not offer.

## 6. MCP tool schemas rejected or misread by Amazon Nova

- **Task:** Give Alexa's tools to Amazon Nova through the Bedrock Converse API.
- **Steps:** Passed the JSON schemas generated by the MCP Python SDK straight into `toolConfig`.
- **Expected:** Accepted as they are by other providers.
- **Actual:** Some shapes the SDK generates (`anyOf: [X, null]` for optional fields, `title` everywhere,
  `default: null`) were rejected or misread.
- **Severity:** Medium.
- **Workaround:** Simplify each schema before sending: collapse optional `anyOf` to the plain type, drop titles and
  null defaults (`_simplify_schema` in `app/agent/providers/bedrock_converse.py`).
- **Suggestion:** Bedrock: accept standard JSON Schema optional patterns, or document the supported subset. MCP SDK:
  an option to emit "provider-friendly" schemas.

## 7. MCP Python SDK: the Streamable HTTP session manager runs only once per process

- **Task:** Test the MCP server's HTTP endpoint inside the FastAPI app.
- **Steps:** Started several FastAPI `TestClient`s with the app's lifespan, which runs `session_manager.run()`.
- **Expected:** Each test can start the server.
- **Actual:** The second start fails: the session manager can only be run once per process.
- **Severity:** Low. Testing only.
- **Workaround:** Web tests skip the lifespan; tool tests use the SDK's in-memory client, which also made the suite fast.
- **Suggestion:** A resettable session manager, or a documented testing pattern for Streamable HTTP servers.

## 8. Google Calendar OAuth setup did not save; test tokens expire after 7 days

- **Task:** Let Alexa read and write my real Google Calendar.
- **Steps:** Went through the Google Auth Platform wizard (branding, audience, contact, policy agreement), then test
  users and a Desktop OAuth client.
- **Expected:** A saved configuration.
- **Actual:** Once, the configuration returned to "not configured yet" with no error. Later, the sign-in silently
  stopped working because refresh tokens for apps in testing mode expire after 7 days.
- **Severity:** Medium.
- **Workaround:** Redid the wizard; signed in again before recording. The app now reports "Google sign-in expired.
  Run: … login" instead of failing silently.
- **Suggestion:** Show save errors in the wizard, and warn clearly about the 7-day token expiry in testing mode.

## 9. Browser speech recognition stops at the first pause

- **Task:** Talk to the simulated Alexa+ app by voice.
- **Steps:** Used the Web Speech API with its default settings.
- **Expected:** "What's… the weather today" is heard as one request.
- **Actual:** Recognition ended at the first short pause, so only "What's" was sent and Alexa asked "What's what?".
- **Severity:** Medium. It made the voice experience feel broken.
- **Workaround:** Continuous recognition with interim results (words shown live), and my own 1.5-second silence timer
  before sending.
- **Suggestion:** Document the conversational set-up (continuous + interim + silence detection) next to the default
  example, which suits short commands better than conversation.
