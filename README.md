# Otomasyon

A small automation-rules dashboard. Create automation rules (with a trigger and
an action), enable/disable them, and "run" them on demand — every run is recorded
in a live run log. Built with a plain **Express** JSON API and a dependency-free
**vanilla JS** frontend so it runs anywhere with just Node.js.

## Tech stack

- **Runtime:** Node.js 20+ (developed on Node 22)
- **Server:** Express 4 (`src/app.js`, `src/server.js`)
- **State:** in-memory store (`src/store.js`) — no database required
- **Frontend:** static HTML/CSS/JS in `public/`
- **Tests:** Jest + Supertest (`test/`)

## Getting started

```bash
npm ci        # install dependencies from the lockfile
npm start     # start the dashboard on http://localhost:3000
```

For development with auto-reload:

```bash
npm run dev
```

## Scripts

| Command        | Description                                  |
| -------------- | -------------------------------------------- |
| `npm start`    | Run the production server (`src/server.js`)  |
| `npm run dev`  | Run with nodemon auto-reload                 |
| `npm test`     | Run the Jest + Supertest suite               |
| `npm run lint` | Lint the codebase with ESLint                |

## API

| Method   | Path                  | Description                         |
| -------- | --------------------- | ----------------------------------- |
| `GET`    | `/api/health`         | Health check                        |
| `GET`    | `/api/rules`          | List automation rules               |
| `POST`   | `/api/rules`          | Create a rule (`name`, `trigger`, `action`) |
| `PATCH`  | `/api/rules/:id`      | Enable/disable a rule (`enabled`)   |
| `DELETE` | `/api/rules/:id`      | Delete a rule                       |
| `POST`   | `/api/rules/:id/run`  | Run a rule (records a run log entry)|
| `GET`    | `/api/runs`           | List recorded runs                  |

`trigger` must be one of `manual`, `schedule`, or `webhook`.

## Cloud Agent environment

This repository ships a [`.cursor/environment.json`](.cursor/environment.json)
that installs dependencies with `npm ci` and starts the dashboard on port 3000.
