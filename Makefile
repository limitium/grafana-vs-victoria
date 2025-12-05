PROJECT?=obs-bench

up:
	docker compose --project-name $(PROJECT) up -d --build

down:
	docker compose --project-name $(PROJECT) down -v

test:
	docker compose --project-name $(PROJECT) run --rm orchestrator python /app/main.py run --all

report:
	docker compose --project-name $(PROJECT) run --rm reporter python /app/main.py

clean:
	rm -rf artifacts/* reports/*

ps:
	docker compose --project-name $(PROJECT) ps

logs:
	docker compose --project-name $(PROJECT) logs -f


