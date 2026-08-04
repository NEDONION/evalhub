import { KeyRound, Plus, Settings2, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import {
  createModelProvider,
  deleteModelProvider,
  getModelProviders,
  testModelProvider,
  updateModelProvider,
} from "../../lib/api";
import type { ModelProvider } from "../../types";
import { Button } from "../ui/Button";
import { FieldMessage } from "../ui/FieldMessage";

interface ProviderSettingsProps {
  model: string;
  modelError?: string;
  providerError?: string;
  onModelChange: (model: string) => void;
  onSelectionChange: (provider: ModelProvider | null) => void;
}

const controlClass =
  "h-10 w-full rounded-md border border-border bg-white px-3 text-sm text-ink shadow-[0_1px_2px_rgba(15,23,42,0.025)] transition-colors placeholder:text-slate-400 hover:border-slate-300 focus:border-primary";

/** 把未知请求错误转换为不包含实现细节的可显示文本。 */
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "模型服务商操作失败";
}

/**
 * 管理脱敏模型服务商配置，并提供可手填、可从 `/models` 建议的模型 ID 输入。
 *
 * @param props 当前远程模型、表单错误和服务商选择回调。
 * @returns 不会预填或回显已保存 API Key 的内联设置区域。
 */
export function ProviderSettings({
  model,
  modelError,
  providerError,
  onModelChange,
  onSelectionChange,
}: ProviderSettingsProps) {
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [selectedId, setSelectedId] = useState("deepseek");
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [managing, setManaging] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftBaseUrl, setDraftBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const selected = providers.find((provider) => provider.id === selectedId) || null;

  useEffect(() => {
    let active = true;
    void getModelProviders()
      .then((items) => {
        if (!active) return;
        const next = items.find((provider) => provider.id === selectedId) || items[0] || null;
        setProviders(items);
        setSelectedId(next?.id || "");
        setDraftName(next?.name || "");
        setDraftBaseUrl(next?.base_url || "");
        setError(null);
        onSelectionChange(next);
      })
      .catch((requestError) => {
        if (!active) return;
        setError(errorMessage(requestError));
        onSelectionChange(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onSelectionChange]);

  /** 切换服务商并清空只属于上一个服务商的密码草稿与模型建议。 */
  function selectProvider(providerId: string) {
    const next = providers.find((provider) => provider.id === providerId) || null;
    setSelectedId(providerId);
    setDraftName(next?.name || "");
    setDraftBaseUrl(next?.base_url || "");
    setApiKey("");
    setModels([]);
    setCreating(false);
    setError(null);
    setNotice(null);
    onSelectionChange(next);
  }

  /** 打开当前配置，密码输入始终保持空白。 */
  function openManager() {
    setDraftName(selected?.name || "");
    setDraftBaseUrl(selected?.base_url || "");
    setApiKey("");
    setCreating(false);
    setManaging(true);
    setError(null);
    setNotice(null);
  }

  /** 切换到自定义服务商创建状态并清空全部配置草稿。 */
  function startCreating() {
    setCreating(true);
    setDraftName("");
    setDraftBaseUrl("");
    setApiKey("");
    setError(null);
    setNotice(null);
  }

  /** 保存公开配置和可选新密钥，再使用已持久化凭据探测模型列表。 */
  async function saveAndTest() {
    if (!draftName.trim() || !draftBaseUrl.trim()) {
      setError("请填写服务商名称和 Base URL");
      return;
    }
    if (creating && !apiKey.trim()) {
      setError("创建自定义服务商时必须填写 API Key");
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);

    let saved: ModelProvider;
    try {
      const input = {
        name: draftName.trim(),
        base_url: draftBaseUrl.trim(),
        api_key: apiKey,
      };
      saved = creating
        ? await createModelProvider(input)
        : await updateModelProvider(selectedId, input);
      setProviders((current) => {
        const exists = current.some((provider) => provider.id === saved.id);
        return exists
          ? current.map((provider) => (provider.id === saved.id ? saved : provider))
          : [...current, saved];
      });
      setSelectedId(saved.id);
      setDraftName(saved.name);
      setDraftBaseUrl(saved.base_url);
      setApiKey("");
      setCreating(false);
      onSelectionChange(saved);
    } catch (requestError) {
      setError(errorMessage(requestError));
      setSaving(false);
      return;
    }

    try {
      const discovered = await testModelProvider(saved.id);
      setModels(discovered);
      setNotice(`配置已保存，发现 ${discovered.length} 个模型`);
    } catch (requestError) {
      setError(`配置已保存；${errorMessage(requestError)}。仍可手填模型 ID。`);
    } finally {
      setSaving(false);
    }
  }

  /** 经二次确认后删除自定义项，或清除内置项覆盖和凭据。 */
  async function removeProvider() {
    if (!selected) return;
    const action = selected.kind === "builtin" ? "重置并清除凭据" : "删除";
    if (!window.confirm(`确认${action} ${selected.name}？`)) return;
    setSaving(true);
    setError(null);
    try {
      await deleteModelProvider(selected.id);
      const items = await getModelProviders();
      const next = items.find((provider) => provider.id === selected.id) || items[0] || null;
      setProviders(items);
      setSelectedId(next?.id || "");
      setDraftName(next?.name || "");
      setDraftBaseUrl(next?.base_url || "");
      setApiKey("");
      setModels([]);
      setManaging(false);
      onSelectionChange(next);
      setNotice(selected.kind === "builtin" ? "已恢复内置默认配置" : "自定义服务商已删除");
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="space-y-3 rounded-md border border-blue-100 bg-blue-50/35 p-4" aria-label="API 模型服务">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="flex items-center gap-2 text-xs font-semibold text-blue-950">
            <KeyRound className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            API 模型服务
          </p>
          <p className="mt-1 text-[11px] leading-5 text-blue-700">密钥加密保存在本机，页面只显示末四位。</p>
        </div>
        <Button size="sm" variant="secondary" onClick={openManager}>
          <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
          管理服务商
        </Button>
      </div>

      <div>
        <label htmlFor="api-provider" className="mb-1.5 block text-xs font-medium text-muted">
          API 服务商
        </label>
        <select
          id="api-provider"
          className={controlClass}
          value={selectedId}
          disabled={loading || providers.length === 0}
          aria-invalid={Boolean(providerError)}
          onChange={(event) => selectProvider(event.target.value)}
        >
          {loading ? <option value="">正在读取服务商</option> : null}
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.name}{provider.kind === "custom" ? " · 自定义" : ""}
            </option>
          ))}
        </select>
        {providerError ? <FieldMessage>{providerError}</FieldMessage> : null}
      </div>

      {selected ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-blue-100 bg-white px-3 py-2">
          <span className="min-w-0 truncate font-mono text-[11px] text-slate-500">{selected.base_url}</span>
          <span
            className={`shrink-0 rounded px-2 py-1 text-[10px] font-semibold ${
              selected.key_configured ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
            }`}
          >
            {selected.key_configured
              ? `已配置 · 尾号 ${selected.key_hint || "••••"}`
              : "尚未配置 API Key"}
          </span>
        </div>
      ) : null}

      <div>
        <label htmlFor="api-model" className="mb-1.5 block text-xs font-medium text-muted">
          模型 ID
        </label>
        <input
          id="api-model"
          list="api-model-options"
          className={`${controlClass} font-mono text-xs`}
          value={model}
          placeholder="例如 deepseek-v4-pro"
          aria-invalid={Boolean(modelError)}
          onChange={(event) => onModelChange(event.target.value)}
        />
        <datalist id="api-model-options">
          {models.map((item) => <option key={item} value={item} />)}
        </datalist>
        {modelError ? <FieldMessage>{modelError}</FieldMessage> : null}
        <p className="mt-1 text-[11px] leading-5 text-slate-500">可从连接验证结果选择，也可直接输入厂商模型 ID。</p>
      </div>

      {managing ? (
        <div className="space-y-3 border-t border-blue-100 pt-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs font-semibold text-ink">{creating ? "新增自定义服务商" : `编辑 ${selected?.name || "服务商"}`}</p>
            {!creating ? (
              <Button size="sm" variant="ghost" onClick={startCreating}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                添加自定义
              </Button>
            ) : null}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="provider-name" className="mb-1.5 block text-xs font-medium text-muted">
                服务商名称
              </label>
              <input
                id="provider-name"
                className={controlClass}
                value={draftName}
                disabled={!creating && selected?.kind === "builtin"}
                onChange={(event) => setDraftName(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="provider-base-url" className="mb-1.5 block text-xs font-medium text-muted">
                Base URL
              </label>
              <input
                id="provider-base-url"
                className={`${controlClass} font-mono text-xs`}
                value={draftBaseUrl}
                onChange={(event) => setDraftBaseUrl(event.target.value)}
              />
            </div>
          </div>
          <div>
            <label htmlFor="provider-api-key" className="mb-1.5 block text-xs font-medium text-muted">
              API Key{!creating && selected?.key_configured ? "（留空保留）" : ""}
            </label>
            <input
              id="provider-api-key"
              type="password"
              autoComplete="new-password"
              className={`${controlClass} font-mono text-xs`}
              value={apiKey}
              placeholder={!creating && selected?.key_configured ? `当前尾号 ${selected.key_hint || "••••"}` : "粘贴 API Key"}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            {!creating && selected ? (
              <Button size="sm" variant="ghost" className="text-red-600 hover:text-red-700" onClick={() => void removeProvider()} disabled={saving}>
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                {selected.kind === "builtin" ? "重置并清除凭据" : "删除服务商"}
              </Button>
            ) : <span />}
            <div className="flex gap-2">
              {creating ? (
                <Button size="sm" variant="ghost" onClick={() => selectProvider(selectedId)} disabled={saving}>
                  取消
                </Button>
              ) : null}
              <Button size="sm" onClick={() => void saveAndTest()} disabled={saving}>
                <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                {saving ? "正在验证" : "保存并验证"}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {error ? <p role="alert" className="text-xs leading-5 text-red-600">{error}</p> : null}
      {notice ? <p aria-live="polite" className="text-xs leading-5 text-emerald-700">{notice}</p> : null}
    </section>
  );
}
