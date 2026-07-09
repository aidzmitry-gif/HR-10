# Модуль hr — контекст для Claude

**Тип:** git submodule → HR-10 (`github.com/aidzmitry-gif/HR-10.git`; правка = коммит в этот репозиторий)
**API-префикс:** `/hr`
**Схема БД:** `hr`
**Статус:** заглушка-каркас (CRUD + канбан подбора, без событий/workflow/прав)

## Назначение
Кадры: учёт штата сотрудников и воронка подбора (найма) кандидатов — от вакансии
до найма, в виде канбан-доски по этапам.

## Файлы
- `module.py` — `HrModule(ModuleContract)`, `get_module()`; в `register` только роутер + виджет.
- `models.py` — ORM: `Employee`, `Candidate` (схема `hr`).
- `schemas.py` — Pydantic: `EmployeeCreate/Out`, `CandidateCreate/Out`, `StageUpdate`.
- `routes.py` — эндпоинты `/hr` (сотрудники + кандидаты + доска).
- `stages.py` — 5 этапов подбора: `new → invite → tech → offer → hired`.

## Что регистрирует в ядре (register())
- Роуты: префикс `/hr`.
- Виджет: `Widget("hr", "HR", source="hr.employees")`.
- Событий, подписок, workflow, permissions, ролей, telegram — **нет**.

## Модель данных (таблицы схемы `hr`)
- `employee` (`Employee`): full_name, position, department, status(`active`), created_at.
- `candidate` (`Candidate`): number, name, position, salary(Numeric 14,2), recruiter,
  priority(`Средний`), **stage** (этап подбора), next_step, created_at.

## API-эндпоинты
- `GET /hr/employees`, `POST /hr/employees` (201) — штат.
- `GET /hr/candidates`, `POST /hr/candidates` (201, авто-номер `CAND-2026-NNNN`).
- `GET /hr/board` — воронка подбора через общий хелпер `core/runtime/funnel.build_board(STAGES, rows, _to_card)`.
- `PATCH /hr/candidates/{id}` — смена этапа (`StageUpdate.stage`).

## Подводные камни / детали
- Роуты сами делают `session.commit()` (отступление от паттерна «транзакцией владеет роут, репозиторий не коммитит»); `core/db/repository.py` не используется — запросы напрямую через `select`.
- `stage` в PATCH **не валидируется** против `STAGES` — можно записать любой.
- Канбан-доска — общий механизм ядра `core/runtime/funnel.py` (`build_board`, `FunnelCard`, `FunnelBoardOut`); тот же используют procurement/production/office/legal/knowledge.
- `salary` в API — float, в ORM — `Decimal`/Numeric (конверсия в `create_candidate`).
