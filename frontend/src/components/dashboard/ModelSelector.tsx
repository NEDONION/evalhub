import { Check, ChevronDown, Download, HardDrive } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { formatBytes } from "../../lib/assets";
import type { ModelOption } from "../../types";

interface ModelSelectorProps {
  id: string;
  label: string;
  options: ModelOption[];
  value: string;
  describedBy?: string;
  onChange: (model: string) => void;
}

const protocolLabels = {
  verified: "Benchmark 已适配",
  static_only: "协议待实测",
  unsupported: "Benchmark 不支持",
} as const;

/** 返回模型选项的简短 Benchmark 协议状态；旧接口缺字段时不展示。 */
function protocolLabel(option: ModelOption): string | null {
  return option.benchmark_protocol ? protocolLabels[option.benchmark_protocol] : null;
}

/**
 * 展示带安装状态、能力标签和容量信息的本地模型选择器。
 *
 * @param props 当前模型、候选模型、可访问性描述和选择回调。
 * @returns 支持鼠标、方向键和首尾跳转的分组列表框。
 */
export function ModelSelector({ id, label, options, value, describedBy, onChange }: ModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const [openAbove, setOpenAbove] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const listboxId = `${id}-${useId().replaceAll(":", "")}-listbox`;
  const installed = options.filter((option) => option.installed);
  const recommended = options.filter((option) => !option.installed);
  const orderedOptions = [...installed, ...recommended];
  const selected = options.find((option) => option.name === value) || null;
  const selectedIndex = Math.max(0, orderedOptions.findIndex((option) => option.name === value));

  useEffect(() => {
    if (!open) return;
    setActiveIndex(selectedIndex);
    optionRefs.current[selectedIndex]?.focus();
  }, [open, selectedIndex]);

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [open]);

  /** 关闭列表，并在键盘操作结束后把焦点归还给触发按钮。 */
  function close(returnFocus = false) {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }

  /** 根据触发器上下可用空间选择列表展开方向，避免候选项被视口裁切。 */
  function showList() {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) {
      const spaceBelow = window.innerHeight - rect.bottom;
      setOpenAbove(spaceBelow < 384 && rect.top > spaceBelow);
    }
    setOpen(true);
  }

  /** 聚焦指定候选项，并把越界索引限制在当前列表内。 */
  function focusOption(index: number) {
    const nextIndex = Math.min(Math.max(index, 0), orderedOptions.length - 1);
    setActiveIndex(nextIndex);
    optionRefs.current[nextIndex]?.focus();
  }

  /** 提交模型选择并收起列表。 */
  function select(option: ModelOption) {
    onChange(option.name);
    close(true);
  }

  /** 处理列表项的方向键、首尾跳转、确认与退出操作。 */
  function handleOptionKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, option: ModelOption) {
    if (event.key === "ArrowDown") focusOption(activeIndex + 1);
    else if (event.key === "ArrowUp") focusOption(activeIndex - 1);
    else if (event.key === "Home") focusOption(0);
    else if (event.key === "End") focusOption(orderedOptions.length - 1);
    else if (event.key === "Enter" || event.key === " ") select(option);
    else if (event.key === "Escape") close(true);
    else return;
    event.preventDefault();
  }

  /** 渲染同一安装状态下的模型候选项。 */
  function renderGroup(title: string, group: ModelOption[], icon: typeof HardDrive) {
    if (group.length === 0) return null;
    const GroupIcon = icon;
    return (
      <div role="presentation">
        <div className="flex items-center gap-2 border-b border-slate-100 bg-slate-50/85 px-3 py-2 text-[10px] font-semibold tracking-[0.1em] text-slate-500 uppercase">
          <GroupIcon className="h-3.5 w-3.5" aria-hidden="true" />
          {title}
          <span className="font-mono text-slate-400">{group.length}</span>
        </div>
        {group.map((option) => {
          const index = orderedOptions.findIndex((item) => item.name === option.name);
          const isSelected = option.name === value;
          const size = formatBytes(option.size_bytes);
          const benchmarkProtocol = protocolLabel(option);
          return (
            <button
              key={option.name}
              ref={(node) => {
                optionRefs.current[index] = node;
              }}
              type="button"
              role="option"
              aria-selected={isSelected}
              onClick={() => select(option)}
              onKeyDown={(event) => handleOptionKeyDown(event, option)}
              className={`relative flex w-full items-start gap-3 border-b border-slate-100 px-4 py-3 text-left outline-none last:border-b-0 hover:bg-blue-50/65 focus:bg-blue-50 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset ${
                isSelected ? "bg-blue-50/45 before:absolute before:inset-y-0 before:left-0 before:w-[3px] before:bg-primary" : "bg-white"
              }`}
            >
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2">
                  <strong className="text-sm font-semibold text-ink">{option.label}</strong>
                  <span className="rounded border border-blue-100 bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700">
                    {option.capability_label}
                  </span>
                  {benchmarkProtocol ? (
                    <span
                      className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600"
                      title={option.benchmark_protocol_reason}
                    >
                      {benchmarkProtocol}
                    </span>
                  ) : null}
                </span>
                <span className="mt-1 block truncate text-xs text-slate-500">{option.description}</span>
                <span className="mt-1.5 block font-mono text-[11px] text-slate-400">
                  {option.name} · {option.size_kind === "estimated" ? "约 " : ""}{size} · {option.installed ? "本机可用" : "需要下载"}
                </span>
              </span>
              <Check className={`mt-1 h-4 w-4 shrink-0 text-primary ${isSelected ? "opacity-100" : "opacity-0"}`} aria-hidden="true" />
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div ref={rootRef} className="relative">
      <span id={`${id}-label`} className="mb-1.5 block text-xs font-medium text-muted">
        {label}
      </span>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-describedby={describedBy}
        disabled={options.length === 0}
        onClick={() => (open ? close() : showList())}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          showList();
        }}
        className="flex min-h-14 w-full items-center gap-3 rounded-md border border-border bg-white px-3 py-2 text-left shadow-[0_1px_2px_rgba(15,23,42,0.025)] outline-none transition-colors hover:border-slate-300 focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-blue-100 disabled:cursor-not-allowed disabled:bg-slate-50"
      >
        {selected ? (
          <>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <strong className="truncate text-sm font-semibold text-ink">{selected.label}</strong>
                <span className="rounded border border-blue-100 bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700">
                  {selected.capability_label}
                </span>
                {protocolLabel(selected) ? (
                  <span
                    className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600"
                    title={selected.benchmark_protocol_reason}
                  >
                    {protocolLabel(selected)}
                  </span>
                ) : null}
              </span>
              <span className="mt-1 block truncate font-mono text-[11px] text-slate-400">
                {selected.size_kind === "estimated" ? "约 " : ""}{formatBytes(selected.size_bytes)} · {selected.installed ? "已安装" : "未下载"}
              </span>
            </span>
            <ChevronDown className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
          </>
        ) : (
          <span className="flex-1 text-sm text-slate-400">暂无可用模型</span>
        )}
      </button>

      {open ? (
        <div
          id={listboxId}
          role="listbox"
          aria-labelledby={`${id}-label`}
          className={`absolute z-30 max-h-96 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-[0_18px_44px_rgba(15,23,42,0.16)] ${
            openAbove ? "bottom-full mb-2" : "mt-2"
          }`}
        >
          {renderGroup("已安装", installed, HardDrive)}
          {renderGroup("推荐下载", recommended, Download)}
        </div>
      ) : null}
    </div>
  );
}
