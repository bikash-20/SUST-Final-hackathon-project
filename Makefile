.PHONY: db-up db-down backend frontend test typecheck lint build verify scenario-a scenario-b scenario-c scenario-d

db-up:
	docker compose up -d --wait postgres

db-down:
	docker compose down

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
	cd frontend && npm run typecheck
	cd frontend && npm run lint

verify: test build

typecheck:
	cd frontend && npm run typecheck

lint:
	cd frontend && npm run lint

build:
	cd frontend && npm run build

scenario-a:
	curl -fsS -X POST http://localhost:8000/v1/simulation/scenario -H 'content-type: application/json' -d '{"scenario":"A","params":{}}'

scenario-b:
	curl -fsS -X POST http://localhost:8000/v1/simulation/scenario -H 'content-type: application/json' -d '{"scenario":"B","params":{}}'

scenario-c:
	curl -fsS -X POST http://localhost:8000/v1/simulation/scenario -H 'content-type: application/json' -d '{"scenario":"C","params":{"provider_id":"nagad"}}'

scenario-d:
	curl -fsS -X POST http://localhost:8000/v1/simulation/scenario -H 'content-type: application/json' -d '{"scenario":"D","params":{"provider_id":"nagad","auto_ack":false}}'
