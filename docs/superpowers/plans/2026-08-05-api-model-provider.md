# API Model Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow EvalHub users to configure encrypted OpenAI-compatible provider credentials in the Web UI and run supported model benchmarks through DeepSeek, SiliconFlow, Kimi, or a custom endpoint.

**Architecture:** Persist sanitized provider profiles and Fernet-encrypted API keys in a dedicated SQLite repository under `.runtime`. Resolve only the selected key when building one `OpenAICompatibleAdapter`; tasks persist `provider_id` and a Base URL snapshot but never a key. Keep provider CRUD and model discovery behind the existing local HTTP server, and isolate the new Web form in a focused `ProviderSettings` component.

**Tech Stack:** Python 3.11, `cryptography` Fernet, SQLite, `urllib.request`, pytest, React 19, TypeScript, Vitest, native HTML `datalist`.

## Global Constraints

- Preserve the existing Ollama and Oracle behavior and old `TaskRequest` JSON compatibility.
- API keys must never appear in browser responses, task JSON, reports, logs, errors, or test snapshots.
- Non-loopback providers require HTTPS; HTTP is allowed only for loopback hosts.
- Provider credential mutation endpoints accept only loopback clients.
- Python changes require accurate types, detailed Chinese docstrings, and explanatory Chinese comments at the repository's required density.
- Tests must not call real provider APIs, download models, or require Ollama.
- Do not modify or stage the user's existing `README.md`, README design changes, or
  `2026-08-05-readme-visitor-first.md` plan.
- Add no dependency except `cryptography`, which is required for authenticated encryption.

---

### Task 1: Encrypted Provider Repository

**Files:**
- Create: `src/evalhub/credentials.py`
- Create: `src/evalhub/model_providers.py`
- Create: `tests/test_credentials.py`
- Create: `tests/test_model_providers.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `CredentialCipher.from_runtime(runtime_dir: Path, env: Mapping[str, str] | None = None) -> CredentialCipher`.
- Produces: `CredentialCipher.encrypt(value: str) -> str` and `CredentialCipher.decrypt(token: str) -> str`.
- Produces: `ModelProviderRepository(path: Path, cipher: CredentialCipher)`.
- Produces: `ModelProviderRepository.list() -> list[ModelProvider]`, `get(provider_id: str) -> ModelProvider`, `save(...) -> ModelProvider`, `delete(provider_id: str) -> None`, and `resolve_api_key(provider_id: str) -> str`.
- Produces: `default_model_provider_repository() -> ModelProviderRepository` for the server and worker process.
- Produces: `normalize_provider_base_url(value: str) -> str`.

- [ ] **Step 1: Add failing credential tests**

```python
def test_generated_key_file_encrypts_without_plaintext(tmp_path: Path) -> None:
    cipher = CredentialCipher.from_runtime(tmp_path, env={})
    token = cipher.encrypt("sk-secret-value")
    assert "sk-secret-value" not in token
    assert cipher.decrypt(token) == "sk-secret-value"
    assert stat.S_IMODE((tmp_path / "provider_credentials.key").stat().st_mode) == 0o600


def test_invalid_environment_key_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(CredentialConfigurationError, match="EVALHUB_CREDENTIAL_KEY"):
        CredentialCipher.from_runtime(tmp_path, env={"EVALHUB_CREDENTIAL_KEY": "invalid"})
```

- [ ] **Step 2: Run credential tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_credentials.py -q`

Expected: collection fails because `evalhub.credentials` does not exist.

- [ ] **Step 3: Add the dependency and minimal cipher**

Add `cryptography>=43.0` to `[project].dependencies`. Implement `CredentialCipher` with `Fernet.generate_key()`, an atomic `os.open(..., O_CREAT | O_EXCL, 0o600)` key-file creation path, strict existing-file permission checks, and `InvalidToken` conversion that preserves the cause without exposing ciphertext.

- [ ] **Step 4: Install the declared dependency**

Run: `.venv/bin/python -m pip install -e ".[dev]"`

Expected: editable install succeeds and `cryptography` imports from the project virtual environment.

- [ ] **Step 5: Run credential tests and confirm GREEN**

Run: `.venv/bin/python -m pytest tests/test_credentials.py -q`

Expected: all credential tests pass.

- [ ] **Step 6: Add failing provider repository tests**

```python
def test_repository_lists_presets_without_exposing_keys(repository: ModelProviderRepository) -> None:
    providers = repository.list()
    assert [item.id for item in providers[:3]] == ["deepseek", "siliconflow", "kimi"]
    assert all(not hasattr(item, "encrypted_api_key") for item in providers)


def test_empty_key_update_preserves_encrypted_credential(repository: ModelProviderRepository) -> None:
    repository.save("deepseek", name="DeepSeek", base_url="https://api.deepseek.com", api_key="sk-first")
    repository.save("deepseek", name="DeepSeek", base_url="https://api.deepseek.com", api_key="")
    assert repository.resolve_api_key("deepseek") == "sk-first"
    assert repository.get("deepseek").key_hint == "irst"
```

- [ ] **Step 7: Run repository tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_model_providers.py -q`

Expected: collection fails because `evalhub.model_providers` does not exist.

- [ ] **Step 8: Implement the smallest provider repository**

Use one `model_providers` table with `id`, `name`, `kind`, `base_url`, `encrypted_api_key`, `key_hint`, `created_at`, and `updated_at`. Merge missing built-ins from this constant rather than inserting them eagerly:

```python
BUILTIN_PROVIDERS = {
    "deepseek": ("DeepSeek", "https://api.deepseek.com"),
    "siliconflow": ("硅基流动", "https://api.siliconflow.cn/v1"),
    "kimi": ("Kimi", "https://api.moonshot.ai/v1"),
}
```

Validate URLs with `urllib.parse.urlparse` and `ipaddress`; use `new_id("provider")` for custom profiles. Deleting a built-in removes its override so the preset reappears with no key; deleting a custom profile removes it completely.

- [ ] **Step 9: Run Task 1 tests and static checks**

Run: `.venv/bin/python -m pytest tests/test_credentials.py tests/test_model_providers.py -q`

Run: `.venv/bin/python -m ruff check src/evalhub/credentials.py src/evalhub/model_providers.py tests/test_credentials.py tests/test_model_providers.py`

Expected: both commands exit 0.

- [ ] **Step 10: Commit Task 1**

```bash
git add pyproject.toml src/evalhub/credentials.py src/evalhub/model_providers.py tests/test_credentials.py tests/test_model_providers.py
git commit -m "feat: store encrypted model providers"
```

---

### Task 2: OpenAI-Compatible Adapter and Task Wiring

**Files:**
- Create: `src/evalhub/adapters/openai_compatible.py`
- Create: `tests/test_openai_compatible_adapter.py`
- Modify: `src/evalhub/adapters/__init__.py`
- Modify: `src/evalhub/cli.py`
- Modify: `src/evalhub/tasks/models.py`
- Modify: `src/evalhub/tasks/workflow.py`
- Modify: `src/evalhub/tasks/executor.py`
- Modify: `tests/test_cli_parser.py`
- Modify: `tests/test_task_executor.py`
- Modify: `tests/test_workflow_repository.py`

**Interfaces:**
- Consumes: `ModelProviderRepository.resolve_api_key(provider_id: str) -> str`.
- Produces: `OpenAICompatibleAdapter(model: str, base_url: str, api_key: str)`.
- Produces: `discover_models(base_url: str, api_key: str) -> list[str]`.
- Changes: `TaskRequest.provider_id: str | None = None`.
- Changes: `build_model_adapter(..., provider_id: str | None = None, provider_repository: ModelProviderRepository | None = None) -> ModelAdapter`.
- Changes: `run_real_benchmark(..., provider_id: str | None = None) -> dict[str, object]`.

- [ ] **Step 1: Add failing Adapter request and response tests**

```python
def test_generate_maps_evalhub_options_to_chat_completions() -> None:
    response = _Response(b'{"choices":[{"message":{"content":"42"}}]}')
    with patch("evalhub.adapters.openai_compatible.urlopen", return_value=response) as opener:
        result = OpenAICompatibleAdapter(
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/",
            api_key="sk-secret",
        ).generate("6 * 7", temperature=0, num_predict=256, seed=7, ignored=True)
    request = opener.call_args.args[0]
    assert request.full_url == "https://api.deepseek.com/chat/completions"
    assert request.get_header("Authorization") == "Bearer sk-secret"
    assert json.loads(request.data) == {
        "model": "deepseek-v4-pro",
        "messages": [{"role": "user", "content": "6 * 7"}],
        "stream": False,
        "temperature": 0,
        "max_tokens": 256,
        "seed": 7,
    }
    assert result == "42"
```

- [ ] **Step 2: Add failing retry, redaction, and discovery tests**

Cover a 429 followed by success, an unretried 401, a 503 exhausting three attempts, malformed `choices`, and `GET {base_url}/models` returning sorted unique IDs. Assert no raised message contains the test API key.

- [ ] **Step 3: Run Adapter tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_openai_compatible_adapter.py -q`

Expected: collection fails because the Adapter module does not exist.

- [ ] **Step 4: Implement the minimal Adapter and discovery function**

Use `urllib.request.Request` and `urlopen`, JSON UTF-8 bodies, a five-minute generation timeout, and a short discovery timeout. Retry only `{429, 500, 502, 503, 504}` up to two extra attempts, honor numeric `Retry-After`, and redact the exact API key from parsed error details.

- [ ] **Step 5: Run Adapter tests and confirm GREEN**

Run: `.venv/bin/python -m pytest tests/test_openai_compatible_adapter.py -q`

Expected: all Adapter tests pass.

- [ ] **Step 6: Add failing task compatibility tests**

```python
def test_openai_adapter_resolves_key_without_persisting_it(provider_repository: ModelProviderRepository) -> None:
    provider_repository.save("deepseek", name="DeepSeek", base_url="https://api.deepseek.com", api_key="sk-secret")
    adapter = build_model_adapter(
        "openai-compatible",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        oracle_responses={},
        provider_id="deepseek",
        provider_repository=provider_repository,
    )
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert "sk-secret" not in repr(adapter)
```

Also assert `TaskRequest` round-trips old JSON without `provider_id`, new workflow benchmark node input contains `provider_id` and the Base URL snapshot, and the executor rejects an API provider on non-native/non-Hexagon-HumanEval paths with an explicit unsupported message.

- [ ] **Step 7: Run task tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_cli_parser.py tests/test_task_executor.py tests/test_workflow_repository.py -q`

Expected: new assertions fail because `provider_id` and the Adapter branch are absent.

- [ ] **Step 8: Wire the Adapter through CLI, workflow, and executor**

Add the new Adapter choice and `--provider-id`. Preserve old constructor compatibility with a trailing `provider_id: str | None = None`. Pass `provider_id` into every `build_model_adapter` call and freeze it into benchmark node inputs. Keep `include_system_cpu` true only for Ollama.

- [ ] **Step 9: Run Task 2 tests and static checks**

Run: `.venv/bin/python -m pytest tests/test_openai_compatible_adapter.py tests/test_cli_parser.py tests/test_task_executor.py tests/test_workflow_repository.py -q`

Run: `.venv/bin/python -m ruff check src/evalhub/adapters src/evalhub/cli.py src/evalhub/tasks tests/test_openai_compatible_adapter.py tests/test_cli_parser.py tests/test_task_executor.py tests/test_workflow_repository.py`

Expected: both commands exit 0.

- [ ] **Step 10: Commit Task 2**

```bash
git add src/evalhub/adapters src/evalhub/cli.py src/evalhub/tasks tests/test_openai_compatible_adapter.py tests/test_cli_parser.py tests/test_task_executor.py tests/test_workflow_repository.py
git commit -m "feat: run benchmarks through API providers"
```

---

### Task 3: Provider HTTP API

**Files:**
- Modify: `src/evalhub/server.py`
- Modify: `tests/test_task_api.py`
- Modify: `tests/test_server_frontend.py`

**Interfaces:**
- Consumes: `ModelProviderRepository` and `discover_models`.
- Produces: `GET/POST /api/model-providers`, `PUT/DELETE /api/model-providers/{id}`, and `POST /api/model-providers/{id}/test`.
- Changes: `_task_request(payload: object, provider_repository: ModelProviderRepository | None = None) -> TaskRequest` resolves the provider Base URL snapshot and validates `provider_id`.

- [ ] **Step 1: Extend the handler test helper and add failing CRUD tests**

Set `handler.client_address = ("127.0.0.1", 12345)` and inject a temporary repository. Assert:

```python
status, body = call_handler(method="GET", path="/api/model-providers", provider_repository=repository)
assert status == 200
assert body["providers"][0] == {
    "id": "deepseek",
    "name": "DeepSeek",
    "kind": "builtin",
    "base_url": "https://api.deepseek.com",
    "key_configured": False,
    "key_hint": None,
    "created_at": None,
    "updated_at": None,
}
```

Add create, update-with-empty-key, built-in reset, custom delete, remote-client rejection, invalid URL, and response-secret scan cases.

- [ ] **Step 2: Add failing model test and evaluation request tests**

Patch `evalhub.server.discover_models` and assert `/test` returns `{"ok": True, "models": [...]}`. Assert an `openai-compatible` evaluation requires `provider_id`, ignores a conflicting payload Base URL in favor of the stored profile, and never stores the API key.

- [ ] **Step 3: Run server tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_server_frontend.py -q`

Expected: new provider routes return 404 and API evaluation validation rejects the Adapter.

- [ ] **Step 4: Implement provider dependency injection and routes**

Add `provider_repository` beside `task_service` on `EvalHubRequestHandler`; construct it in `serve()` with the project `.runtime` directory. Add tight route parsers, `do_PUT`, expanded `do_DELETE`, and `_require_loopback_client()` using `ipaddress.ip_address(self.client_address[0]).is_loopback`.

- [ ] **Step 5: Implement task request validation**

Accept `adapter in {"ollama", "oracle", "openai-compatible"}`. Require a configured provider for API model evaluations, set `base_url` from the stored provider, reject `provider_id` for other adapters, and preserve the existing Agent/Ollama checks.

- [ ] **Step 6: Run Task 3 tests and static checks**

Run: `.venv/bin/python -m pytest tests/test_task_api.py tests/test_server_frontend.py -q`

Run: `.venv/bin/python -m ruff check src/evalhub/server.py tests/test_task_api.py tests/test_server_frontend.py`

Expected: both commands exit 0.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/evalhub/server.py tests/test_task_api.py tests/test_server_frontend.py
git commit -m "feat: expose model provider settings API"
```

---

### Task 4: Web Provider Settings and API Model Selection

**Files:**
- Create: `frontend/src/components/dashboard/ProviderSettings.tsx`
- Create: `frontend/src/components/dashboard/ProviderSettings.test.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/lib/evaluation.ts`
- Modify: `frontend/src/lib/evaluation.test.ts`
- Modify: `frontend/src/components/dashboard/EvaluationForm.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Produces: `ModelProvider`, `ModelProviderInput`, `ModelProvidersResponse`, and `ProviderTestResponse` TypeScript types.
- Produces: `getModelProviders`, `createModelProvider`, `updateModelProvider`, `deleteModelProvider`, and `testModelProvider` API functions.
- Produces: `ProviderSettings({ value, onChange, disabled })`, where `value` contains `providerId`, `baseUrl`, and `model`.
- Changes: `AdapterType` includes `"openai-compatible"`; `EvaluationRequest.provider_id?: string`.

- [ ] **Step 1: Add failing frontend API tests**

```typescript
it("never adds an API key to a provider read request", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ providers: [] }));
  vi.stubGlobal("fetch", fetchMock);
  await getModelProviders();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/model-providers",
    expect.objectContaining({ headers: { "Content-Type": "application/json" } }),
  );
});

it("updates and tests a provider with encoded routes", async () => {
  const provider = {
    id: "custom/one",
    name: "Custom",
    kind: "custom" as const,
    base_url: "https://models.example.com/v1",
    key_configured: true,
    key_hint: "last",
    created_at: "2026-08-05T00:00:00+00:00",
    updated_at: "2026-08-05T00:00:00+00:00",
  };
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse({ ok: true, provider }))
    .mockResolvedValueOnce(jsonResponse({ ok: true, models: ["model-a"] }));
  vi.stubGlobal("fetch", fetchMock);
  await updateModelProvider("custom/one", { name: "Custom", base_url: provider.base_url, api_key: "" });
  await testModelProvider("custom/one");
  expect(fetchMock.mock.calls.map(([url, options]) => [url, options.method])).toEqual([
    ["/api/model-providers/custom%2Fone", "PUT"],
    ["/api/model-providers/custom%2Fone/test", "POST"],
  ]);
});
```

- [ ] **Step 2: Run API tests and confirm RED**

Run: `npm --prefix frontend test -- --run src/lib/api.test.ts`

Expected: imports fail because provider API functions do not exist.

- [ ] **Step 3: Add provider types and API functions**

Keep `fetchJson` as the single request boundary. Send API Key only in create/update bodies; the TypeScript response type has no `api_key` property.

- [ ] **Step 4: Run API tests and confirm GREEN**

Run: `npm --prefix frontend test -- --run src/lib/api.test.ts`

Expected: the API test file passes.

- [ ] **Step 5: Add failing request-builder tests**

```typescript
it("builds an API provider request without credentials", () => {
  expect(buildEvaluationRequest({
    ...baseValues,
    adapter: "openai-compatible",
    providerId: "deepseek",
    model: "deepseek-v4-pro",
    baseUrl: "https://api.deepseek.com",
  })).toMatchObject({
    adapter: "openai-compatible",
    provider_id: "deepseek",
    model: "deepseek-v4-pro",
    base_url: "https://api.deepseek.com",
  });
});
```

Assert Agent requests omit `provider_id` and force Ollama.

- [ ] **Step 6: Run request-builder tests and confirm RED**

Run: `npm --prefix frontend test -- --run src/lib/evaluation.test.ts`

Expected: the API provider assertion fails because `provider_id` is absent.

- [ ] **Step 7: Implement request-builder changes**

Add `providerId: string | null` to `EvaluationFormValues`; include it only for the API Adapter. Preserve all existing subject, suite, limit, and Agent rules.

- [ ] **Step 8: Add failing ProviderSettings interaction tests**

Render the component with API mocks and assert:

- built-in profiles appear immediately;
- password inputs are blank even when `key_configured` is true;
- saving sends the typed key once and clears the field;
- “保存并验证” populates a native `datalist` and calls `onChange` with the selected model;
- model ID remains editable when discovery fails;
- deleting a custom profile asks for confirmation and refreshes the list.

- [ ] **Step 9: Run component tests and confirm RED**

Run: `npm --prefix frontend test -- --run src/components/dashboard/ProviderSettings.test.tsx`

Expected: collection fails because the component does not exist.

- [ ] **Step 10: Implement the focused provider component**

Use existing control classes, visible labels, password `autocomplete="new-password"`, native `<input list="provider-model-options">`, and a compact connection card showing Base URL plus `已配置 · ••••{key_hint}`. Do not create a custom dropdown or modal.

- [ ] **Step 11: Integrate the component into EvaluationForm**

Add `API 服务` to the existing Adapter control. Show `ProviderSettings` only for model evaluations using `openai-compatible`; keep Ollama's `ModelSelector`, install validation, Base URL field, and asset action unchanged. Disable submission until provider ID and model are present. Switching to Agent restores Ollama.

- [ ] **Step 12: Run form and App tests**

Run: `npm --prefix frontend test -- --run src/components/dashboard/ProviderSettings.test.tsx src/lib/evaluation.test.ts src/App.test.tsx`

Expected: all selected frontend tests pass with no warnings.

- [ ] **Step 13: Run frontend typecheck/build**

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

Expected: both commands exit 0.

- [ ] **Step 14: Commit Task 4**

```bash
git add frontend/src/types.ts frontend/src/lib/api.ts frontend/src/lib/api.test.ts frontend/src/lib/evaluation.ts frontend/src/lib/evaluation.test.ts frontend/src/components/dashboard/ProviderSettings.tsx frontend/src/components/dashboard/ProviderSettings.test.tsx frontend/src/components/dashboard/EvaluationForm.tsx frontend/src/App.test.tsx
git commit -m "feat: configure API providers in the console"
```

---

### Task 5: Documentation and Full Verification

**Files:**
- Modify: `.env.example`
- Modify: `docs/getting-started/20260804_本地运行指南.md`
- Modify: `docs/architecture/20260804_API接口草案.md`
- Modify: `docs/architecture/20260804_系统架构.md`

**Interfaces:**
- Documents: `EVALHUB_CREDENTIAL_KEY`, the three presets, custom providers, API routes, security limits, and support matrix.

- [ ] **Step 1: Add only safe configuration examples**

Add a generated dummy Fernet-shaped value to `.env.example`:

```dotenv
EVALHUB_CREDENTIAL_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
```

State that users normally may omit it for a local `0600` key file and must replace the dummy value before explicit environment injection. Do not add provider API keys to `.env.example`.

- [ ] **Step 2: Update user and architecture documentation**

Document the Web flow, encrypted-at-rest boundary, loopback-only credential writes, native/Hexagon support, Agent/Ollama restriction, service endpoints, `provider_id`, and the separate provider repository.
Leave `README.md` untouched because it contains user changes outside this task.

- [ ] **Step 3: Run focused Python tests**

Run: `.venv/bin/python -m pytest tests/test_credentials.py tests/test_model_providers.py tests/test_openai_compatible_adapter.py tests/test_task_api.py tests/test_task_executor.py tests/test_workflow_repository.py -q`

Expected: all focused tests pass.

- [ ] **Step 4: Run the full Python suite**

Run: `.venv/bin/python -m pytest`

Expected: zero failures.

- [ ] **Step 5: Run Python lint**

Run: `.venv/bin/python -m ruff check .`

Expected: zero findings.

- [ ] **Step 6: Run the full frontend suite and build**

Run: `npm --prefix frontend test -- --run`

Run: `npm --prefix frontend run build`

Expected: all tests pass and the production bundle builds.

- [ ] **Step 7: Verify repository hygiene**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors; only task files plus the user's pre-existing README and README design changes remain.

- [ ] **Step 8: Commit documentation**

```bash
git add .env.example docs/getting-started/20260804_本地运行指南.md docs/architecture/20260804_API接口草案.md docs/architecture/20260804_系统架构.md
git commit -m "docs: explain API model providers"
```
