import { useConfigUI } from '../../configStore';
import { AppSettings } from './AppSettings';
import { BotSettingsModal } from './BotSettingsModal';

/** Overlays owned by the settings feature: bot settings dialog and the app settings page. */
export function ConfigLayer() {
  const botSettings = useConfigUI((s) => !!s.botSettings);
  const appSettings = useConfigUI((s) => !!s.appSettings);
  return (
    <>
      {appSettings && <AppSettings />}
      {botSettings && <BotSettingsModal />}
    </>
  );
}
