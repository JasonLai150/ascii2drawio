import { useEffect, useRef, useState } from "react";
import {
  convert,
  health,
  listExamples,
  type Enhance,
  type Example,
  type Report,
} from "./api";
import { DrawioPreview } from "./DrawioPreview";

const DEFAULT_DIAGRAM = `┌─────────┐         ┌─────────┐
│  Alpha  │────────>│  Beta   │
└─────────┘         └─────────┘
     │                   │
     │                   ↓
     │              ┌─────────┐
     └─────────────>│  Gamma  │
                    └─────────┘`;

const DETERMINISTIC: Enhance = { repair: false, labels: false };
const FULL_AI: Enhance = { repair: true, labels: true };

export default function App() {
  const [text, setText] = useState(DEFAULT_DIAGRAM);
  const [xml, setXml] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [enhancing, setEnhancing] = useState(false);
  const [enhanced, setEnhanced] = useState(false);
  const [llmAvailable, setLlmAvailable] = useState(false);
  const [examples, setExamples] = useState<Example[]>([]);
  const editedXml = useRef(""); // latest XML, including in-preview edits
  const seq = useRef(0); // drop stale responses (typing races AI, etc.)

  // Resizable split between editor and preview.
  const mainRef = useRef<HTMLElement>(null);
  const [leftWidth, setLeftWidth] = useState(480);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return;
    function onMove(e: MouseEvent) {
      const el = mainRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const w = e.clientX - rect.left;
      setLeftWidth(Math.max(200, Math.min(rect.width - 200, w)));
    }
    function onUp() {
      setDragging(false);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging]);

  useEffect(() => {
    listExamples().then(setExamples).catch(() => {});
    health().then((h) => setLlmAvailable(h.llm)).catch(() => {});
  }, []);

  async function run(enhance: Enhance) {
    const id = ++seq.current;
    const isAI = enhance.repair || enhance.labels;
    if (isAI) setEnhancing(true);
    else setBusy(true);
    setError("");
    try {
      const r = await convert(text, enhance);
      if (id !== seq.current) return; // a newer request superseded this one
      setXml(r.xml);
      setReport(r.report);
      setEnhanced(isAI);
      editedXml.current = r.xml;
    } catch (e) {
      if (id !== seq.current) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === seq.current) {
        setBusy(false);
        setEnhancing(false);
      }
    }
  }

  // Debounced deterministic conversion as the user types (free + fast).
  // Editing always invalidates a prior AI-enhanced result.
  useEffect(() => {
    const handle = setTimeout(() => run(DETERMINISTIC), 350);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  function download() {
    const data = editedXml.current || xml;
    if (!data) return;
    const blob = new Blob([data], { type: "application/xml" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "diagram.drawio";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  const statusText = error
    ? `error: ${error}`
    : enhancing
      ? "enhancing with AI…"
      : busy
        ? "converting…"
        : report
          ? `${report.nodes} nodes · ${report.edges} edges · ${report.orphan_clusters} orphan region(s)` +
            (enhanced ? "  ·  ✨ AI-enhanced" : "")
          : "";

  return (
    <div className="app">
      <section className="hero">
        <h1>ascii2diagram</h1>
        <p className="tagline">ASCII to draw.io converter</p>
        <p className="lead">
          Turn the ASCII &amp; Unicode box-drawing diagrams that ChatGPT, Claude
          and other LLMs love to emit into editable{" "}
          <strong>draw.io / diagrams.net</strong> files — with your boxes,
          arrows and labels kept exactly where you drew them.
        </p>
        <ol className="steps">
          <li className="step">
            <span className="num">1</span>
            <div>
              <strong>Paste your diagram</strong>
              <p>
                Drop in any text diagram made of <code>┌─┐ │ └─┘</code> boxes and{" "}
                <code>──&gt; ↓</code> arrows.
              </p>
            </div>
          </li>
          <li className="step">
            <span className="num">2</span>
            <div>
              <strong>It’s parsed, not re-drawn</strong>
              <p>
                A deterministic parser pins every box to its original position so
                the result looks like your ASCII — not a re-laid-out graph.
              </p>
            </div>
          </li>
          <li className="step">
            <span className="num">3</span>
            <div>
              <strong>Preview &amp; download</strong>
              <p>
                Edit live, optionally <em>✨ Enhance with AI</em> to recover
                missed boxes &amp; labels, then download a real{" "}
                <code>.drawio</code> file.
              </p>
            </div>
          </li>
        </ol>
      </section>

      <div className="toolbar">
        <span className={"status" + (error ? " err" : "")}>{statusText}</span>
        <span className="grow" />
        <select
          value=""
          onChange={(e) => {
            const ex = examples.find((x) => x.name === e.target.value);
            if (ex) setText(ex.text);
          }}
        >
          <option value="">Load example…</option>
          {examples.map((x) => (
            <option key={x.name} value={x.name}>
              {x.name}
            </option>
          ))}
        </select>
        <button
          className="enhance"
          onClick={() => run(FULL_AI)}
          disabled={!llmAvailable || busy || enhancing || !xml}
          title={
            llmAvailable
              ? "Use AI to recover missed boxes and fix labels"
              : "AI enhance is not configured on the server"
          }
        >
          {enhancing ? "Enhancing…" : "✨ Enhance with AI"}
        </button>
        <button onClick={download} disabled={!xml}>
          Download .drawio
        </button>
      </div>

      <div className="workspace">
        <main ref={mainRef}>
          <textarea
            className="editor"
            style={{ width: leftWidth }}
            spellCheck={false}
            wrap="off"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste an ASCII / Unicode box-drawing diagram…"
          />
          <div
            className="gutter"
            onMouseDown={() => setDragging(true)}
            title="Drag to resize"
          />
          <div className="preview">
            <DrawioPreview xml={xml} onEdited={(x) => (editedXml.current = x)} />
          </div>
          {/* While dragging, this overlay keeps mouse events off the iframe. */}
          {dragging && <div className="drag-overlay" />}
        </main>
      </div>

      <footer>
        Diagrams are sent to the server to convert. “Enhance with AI” also sends
        the diagram to Google (Gemini). Open-source on{" "}
        <a href="https://github.com/JasonLai150/ascii2drawio">GitHub</a>.
      </footer>
    </div>
  );
}
