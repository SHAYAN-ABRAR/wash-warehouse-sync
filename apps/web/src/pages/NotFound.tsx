import { Link } from 'react-router-dom'
import { Compass } from 'lucide-react'
import { EmptyState, PageHeader } from '@/components/common'
import { Button } from '@/components/ui/button'

export default function NotFound() {
  return (
    <>
      <PageHeader title="Page not found" />
      <EmptyState
        icon={Compass}
        title="Nothing at this address"
        description="This address does not exist in WASH Warehouse Sync."
        action={
          <Button asChild>
            <Link to="/">Back to the dashboard</Link>
          </Button>
        }
        className="mt-4"
      />
    </>
  )
}
