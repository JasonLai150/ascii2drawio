export interface Report {
  nodes: number;
  edges: number;
  orphan_clusters: number;
}

export interface Enhance {
  repair: boolean;
  labels: boolean;
}

export interface ConvertResponse {
  xml: string;
  report: Report;
}

export async function convert(
  text: string,
  enhance: Enhance = { repair: false, labels: false },
): Promise<ConvertResponse> {
  const res = await fetch("/api/convert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, enhance }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

export interface Example {
  name: string;
  text: string;
}

export async function listExamples(): Promise<Example[]> {
  const res = await fetch("/api/examples");
  if (!res.ok) return [];
  return res.json();
}

export interface Health {
  status: string;
  llm: boolean;
}

export async function health(): Promise<Health> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error("health check failed");
  return res.json();
}
