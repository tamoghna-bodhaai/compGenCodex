"use client";

import { useEffect, useRef } from "react";
import { Icon } from "@/components/icons";
import s from "@/styles/ui.module.css";

export function Dialog({ open, title, subtitle, onClose, children, footer, wide = false }: { open: boolean; title: string; subtitle?: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  if (!open) return null;
  return (
    <dialog ref={ref} className={`${s.dialog} ${wide ? s.dialogWide : ""}`} onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <header className={s.modalHead}>
        <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
        <button className={`${s.button} ${s.iconButton}`} type="button" onClick={onClose} aria-label="Close dialog"><Icon name="close" /></button>
      </header>
      {children}
      {footer && <footer className={s.modalFooter}>{footer}</footer>}
    </dialog>
  );
}
