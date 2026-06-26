declare module "@decky/ui" {
  import type { ComponentType, ReactNode } from "react";

  export const ButtonItem: ComponentType<{
    children?: ReactNode;
    layout?: string;
    onClick?: () => void;
  }>;
  export const PanelSection: ComponentType<{
    children?: ReactNode;
    title?: string;
  }>;
  export const PanelSectionRow: ComponentType<{
    children?: ReactNode;
  }>;
  export const staticClasses: {
    Title: string;
  };
}

interface Window {
  SteamClient?: {
    Settings?: {
      GetCurrentLanguage?: () => Promise<string>;
    };
  };
}
