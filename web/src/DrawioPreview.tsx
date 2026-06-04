import { useEffect, useRef } from "react";

// embed.diagrams.net in JSON-protocol embed mode. We postMessage the XML to load
// it; the editor posts back `autosave` events as the user edits, which we lift
// up so the Download button always grabs the latest (possibly edited) XML.
const EMBED_URL =
  "https://embed.diagrams.net/?embed=1&proto=json&spin=1&ui=min&libraries=0&noSaveBtn=1";

interface Props {
  xml: string;
  onEdited?: (xml: string) => void;
}

export function DrawioPreview({ xml, onEdited }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const xmlRef = useRef(xml);
  xmlRef.current = xml;
  const ready = useRef(false);

  function load(xmlToLoad: string) {
    const win = iframeRef.current?.contentWindow;
    if (win && xmlToLoad) {
      win.postMessage(
        JSON.stringify({ action: "load", autosave: 1, xml: xmlToLoad }),
        "*",
      );
    }
  }

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (typeof e.data !== "string") return;
      let msg: { event?: string; xml?: string };
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.event === "init") {
        ready.current = true;
        load(xmlRef.current); // load whatever XML we have once the editor is up
      } else if ((msg.event === "autosave" || msg.event === "save") && msg.xml) {
        onEdited?.(msg.xml);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Reload when the converted XML changes (only after the editor has init'd).
  useEffect(() => {
    if (ready.current) load(xml);
  }, [xml]);

  return (
    <iframe ref={iframeRef} src={EMBED_URL} title="draw.io preview" />
  );
}
