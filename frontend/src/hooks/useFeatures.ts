import { useQuery } from '@tanstack/react-query'
import * as endpoints from '../api/endpoints'

/** Which optional parts are switched on (FEATURE_BILLING / FEATURE_AI on the server).
 *  While loading, both count as off so paid/AI screens never flash in personal mode. */
export function useFeatures() {
  const query = useQuery({ queryKey: ['features'], queryFn: endpoints.features, staleTime: Infinity })
  return { billing: query.data?.billing ?? false, ai: query.data?.ai ?? false, google: query.data?.google ?? false, loaded: query.isSuccess }
}
