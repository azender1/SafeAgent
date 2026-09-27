# v15 public source manifest

The sanitized v15 public package prepared for publication contains these source/test files:

- alpaca_api_adapter.py
- alpaca_orders_parser.py
- bipartite_solver_reference.py
- control_cli.py
- ingestion_normalization.py
- output_validator_v12.py
- reconcile_v12.py
- reconcile_v12_classify.py
- reference_model_v12.py
- regression_fixtures_v12.py
- safeagent_claims_parser.py
- safeagent_db_adapter.py
- test_alpaca_api_adapter.py
- test_bipartite_solver.py
- test_parsers_and_cli.py
- test_safeagent_db_adapter.py
- test_v12_full_suite.py
- requirements.txt
- mutants/generate_mutants.py
- mutants/mutation_runner.py
- fixtures/alpaca_orders_synthetic_sample.json
- fixtures/safeagent_claims_sample.json
- fixtures/safeagent_orders_synthetic_sample.db
- fixtures/synthetic_bot_sample.log

Excluded from the public package: private acceptance reports, private evidence outputs, and the prior README/BUILD_STATUS text that described the private Alpaca acceptance exercise.

Sanitized package SHA-256: `5cc871b69c5e24f944392bf8999f4615adf1fd44f5910105ceb8494b25f137df`

Synthetic database SHA-256: `851e5aa5efd09992e227d200cbe82ede9c0b08913aa02ce4595432200f7024fd`

Synthetic five-line log SHA-256: `33fd2234d026b16df9593d232eab44585d1084e912fc86d734ffa71d5113e60b`
