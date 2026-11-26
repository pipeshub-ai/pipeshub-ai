export interface PersonPanelExtrasProps {
  /** The person whose profile panel is open. */
  userId: string;
  name: string;
  /** The panel shows the signed-in person themselves. */
  isSelf: boolean;
}

/** A slot on a person's profile panel an edition may fill; this build shows nothing there. */
export function PersonPanelExtras(_props: PersonPanelExtrasProps): null {
  return null;
}
