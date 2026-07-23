.PHONY: help install seed api ui test reset clean

help:
	@echo "  make install   install python + node dependencies"
	@echo "  make seed      generate reference data and the 7 test submissions"
	@echo "  make api       run the backend on :8001"
	@echo "  make ui        run the frontend on :5174"
	@echo "  make test      run the test suite"
	@echo "  make eval          precision / recall on the 11 golden cases"
	@echo "  make eval-volume   the same metrics on 250 generated cases (incl. plausible fraud)"
	@echo "  make calibrate     sweep the screening threshold and show the tradeoff curve"
	@echo "  make serve         build UI + serve the whole product on one URL (:8001)"
	@echo "  make docker        build & run the deployable container (:8000)"
	@echo "  make reset         clear case history"
	@echo ""
	@echo "  first time:    make install && make seed"
	@echo "  then, in two terminals:  make api   |   make ui"
	@echo ""
	@echo "  ports are 8001/5174 so this can run alongside ap-invoice-agent (8000/5173)"

install:
	python -m pip install -r backend/requirements.txt
	cd frontend && npm install

seed:
	python scripts/build_fixtures.py

api:
	python -m uvicorn backend.app.api.app:app --reload --host 127.0.0.1 --port 8001

ui:
	cd frontend && npm run dev

test:
	python -m pytest tests/ -q

eval:
	python scripts/evaluate.py

eval-volume:
	python scripts/eval_volume.py 250

calibrate:
	python scripts/calibrate.py screening

# Build the frontend and serve everything from one FastAPI process on :8001 —
# the same single-URL shape the Docker image / deployed link uses.
serve:
	cd frontend && npm run build
	python -m uvicorn backend.app.api.app:app --host 127.0.0.1 --port 8001

# Build and run the deployable container locally (needs Docker).
docker:
	docker build -t vendor-onboarding .
	docker run -p 8000:8000 vendor-onboarding

reset:
	curl -s -X POST http://127.0.0.1:8001/v1/reset || \
		python -c "import sys; sys.path.insert(0,'.'); from backend.app.storage import db; print(db.reset_db())"

clean:
	rm -rf data/cases.db data/cases.db-* data/.llm_cache .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
