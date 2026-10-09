# Friction log

Problems hit while building with Amazon developer tools, as the hackathon asks:
task, steps, expected vs actual, severity, workaround, suggestion.

## 1. Amazon Location `CalculateRoutes` replies omit members the service model marks required

- **Task:** Unit-test our Amazon Location provider offline with botocore's `Stubber`, replaying a real recorded `geo-routes calculate-routes` response.
- **Steps:** Recorded a real Transit response (Central Station to Sydney Airport, `--max-alternatives 2`) with the AWS CLI. Passed it to `Stubber.add_response("calculate_routes", ...)`.
- **Expected:** A real response from the service validates against the service's own model.
- **Actual:** `ParamValidationError`: missing required `Routes[].MajorRoadLabels`, `PedestrianLegDetails.AfterTravelSteps`, `PedestrianLegDetails.Notices`, and `TurnStepDetails.Intersection`. The real service leaves out these members when they are empty, but the model marks them required.
- **Severity:** Low. Testing only.
- **Workaround:** Fill the missing members with empty lists before stubbing (`tests/test_travel_search.py::_as_stub_reply`).
- **Suggestion:** Either always return the required members (as empty lists), or mark them optional in the model so recorded real responses can be replayed in tests.

## 2. Latest boto3 for Python 3.9 cannot request Transit routes

- **Task:** Call `CalculateRoutes` with `TravelMode="Transit"` from Python.
- **Steps:** `pip install boto3` on Python 3.9.13, which gave boto3 1.42.97. Checked the `geo-routes` service model.
- **Expected:** The same travel modes the AWS CLI accepts (the CLI accepts `Transit` and our recordings were made with it).
- **Actual:** `RouteTravelMode` has no `Transit` in that boto3. It is available in boto3 1.43.109 on Python 3.11.
- **Severity:** Medium. A silent capability gap with no error until you call it.
- **Workaround:** Project environment on Python 3.11 (`uv venv --python 3.11`).
- **Suggestion:** Say in the Amazon Location docs which SDK versions support Transit routing. A clear error ("Transit requires botocore ≥ X") would help more than a generic validation failure.
