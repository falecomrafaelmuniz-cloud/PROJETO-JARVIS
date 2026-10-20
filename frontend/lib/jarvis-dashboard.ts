export type DashboardNotificationLevel = 'info' | 'warning' | 'error';
export type DashboardLocationStatus = 'offline' | 'requesting' | 'shared' | 'unavailable';

export interface DashboardCoordinates {
  latitude: number;
  longitude: number;
  country?: string;
  state?: string;
  city?: string;
}

export interface DashboardNotification {
  id: string;
  message: string;
  detail?: string;
  level: DashboardNotificationLevel;
  createdAt: number;
}

const NOTIFICATION_EVENT = 'jarvis:dashboard-notification';
const LOCATION_EVENT = 'jarvis:dashboard-location';
const COORDINATES_EVENT = 'jarvis:dashboard-coordinates';

export function notifyDashboard(
  message: string,
  level: DashboardNotificationLevel = 'info',
  detail?: string
) {
  if (typeof window === 'undefined') return;

  const notification: DashboardNotification = {
    id: `${Date.now()}-${Math.random()}`,
    message,
    detail,
    level,
    createdAt: Date.now(),
  };
  window.dispatchEvent(new CustomEvent(NOTIFICATION_EVENT, { detail: notification }));
}

export function setDashboardLocationStatus(status: DashboardLocationStatus) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(LOCATION_EVENT, { detail: status }));
}

export function setDashboardCoordinates(coordinates: DashboardCoordinates | null) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(COORDINATES_EVENT, { detail: coordinates }));
}

export const dashboardEvents = {
  location: LOCATION_EVENT,
  notification: NOTIFICATION_EVENT,
  coordinates: COORDINATES_EVENT,
};
