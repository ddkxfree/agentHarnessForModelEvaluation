import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { StudioProvider } from "./state/Studio";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("missing root");

createRoot(root).render(
  <StrictMode>
    <StudioProvider>
      <App />
    </StudioProvider>
  </StrictMode>,
);
