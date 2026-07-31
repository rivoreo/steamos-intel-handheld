declare module "@decky/ui" {
  import type { ComponentType, ReactNode } from "react";

  interface ItemProps {
    label?: ReactNode;
    description?: ReactNode;
    children?: ReactNode;
    layout?: "below" | "inline";
    bottomSeparator?: "standard" | "thick" | "none";
    indentLevel?: number;
    tooltip?: string;
  }

  export const ButtonItem: ComponentType<
    ItemProps & {
      onClick?: () => void;
      disabled?: boolean;
    }
  >;
  export const PanelSection: ComponentType<{
    children?: ReactNode;
    title?: string;
  }>;
  export const PanelSectionRow: ComponentType<{
    children?: ReactNode;
  }>;
  export const Field: ComponentType<ItemProps & { focusable?: boolean }>;
  export const ToggleField: ComponentType<
    ItemProps & {
      checked: boolean;
      disabled?: boolean;
      onChange?: (checked: boolean) => void;
    }
  >;
  export const SliderField: ComponentType<
    ItemProps & {
      value: number;
      min?: number;
      max?: number;
      step?: number;
      notchCount?: number;
      notchLabels?: { notchIndex: number; label: string; value?: number }[];
      notchTicksVisible?: boolean;
      showValue?: boolean;
      disabled?: boolean;
      valueSuffix?: string;
      onChange?: (value: number) => void;
    }
  >;
  export const DropdownItem: ComponentType<
    ItemProps & {
      rgOptions: { data: string; label: ReactNode }[];
      selectedOption: string;
      disabled?: boolean;
      strDefaultLabel?: string;
      onChange?: (option: { data: string; label: ReactNode }) => void;
    }
  >;
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
