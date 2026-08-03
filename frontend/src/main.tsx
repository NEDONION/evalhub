import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles.css";

const root = document.querySelector<HTMLDivElement>("#root");

if (!root) {
  throw new Error("缺少 React 根节点");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
