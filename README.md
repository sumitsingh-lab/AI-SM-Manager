# AI Social Media Manager

Monorepo starter with:

- `client`: Next.js, TypeScript, Tailwind CSS, and Shadcn UI-ready components.
- `server`: Python FastAPI backend with Prisma schema targeting PostgreSQL.

## Prerequisites

- Node.js 18+
- Python 3.10+
- PostgreSQL

## Setup

```bash
npm run setup
```

Copy `.env.example` to `.env` and `server/.env`, then adjust `DATABASE_URL` for your PostgreSQL database.

```bash
cp .env.example .env
cp server/.env.example server/.env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
Copy-Item server/.env.example server/.env
```

Generate the Prisma client:

Generate a token encryption key and set `TOKEN_ENCRYPTION_KEY` in both env files:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then generate the Prisma client:

```bash
npm run prisma:generate
```

Create and apply the initial migration:

```bash
npm run prisma:migrate
```

Start both dev servers:

```bash
npm run dev
```

- Client: http://localhost:3000
- Server: http://localhost:8000
- API docs: http://localhost:8000/docs

## Prisma Schema

The complete Prisma schema lives at `server/prisma/schema.prisma`.
