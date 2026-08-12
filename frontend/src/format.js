export function formatBytes(bytes, withUnitSpan = false) {
  const value = Number(bytes) || 0;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  const num = i === 0 ? String(n) : n.toFixed(n >= 100 ? 0 : n >= 10 ? 1 : 2);
  if (withUnitSpan) {
    return `${num}<span class="unit">${units[i]}</span>`;
  }
  return `${num} ${units[i]}`;
}

export function timeAgo(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (diffSec < 10) return "刚刚";
  if (diffSec < 60) return `${diffSec} 秒前`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.round(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.round(diffHour / 24);
  return `${diffDay} 天前`;
}

export function formatLatency(ms, fallback = "未测试") {
  const value = Number(ms);
  return ms == null || Number.isNaN(value) ? fallback : `${value.toLocaleString("zh-CN")} ms`;
}

export function formatPercent(value, digits = 0) {
  const number = Number(value);
  return `${(Number.isFinite(number) ? number : 0).toFixed(digits)}%`;
}
