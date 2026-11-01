import React from 'react';
import type { DeletableItemType } from '../utils/feed-deletion';
interface DeleteButtonProps { itemType: DeletableItemType; id: number; onDeleted: (itemType: DeletableItemType, id: number) => void }
/** The original sources remain managed by their existing services. */
export function DeleteButton(_props: DeleteButtonProps) { return <span title="原来源只读" className="card-project">只读</span>; }
