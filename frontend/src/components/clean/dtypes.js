/* Turning a pandas dtype into something a non-programmer recognises.
 *
 * Its own module rather than living beside the components: a file that
 * exports both components and plain helpers breaks React Fast Refresh, so
 * the section files reload cleanly with this split out.
 */

export function friendlyDtype(dtype) {
  if (dtype.startsWith('int') || dtype.startsWith('float')) return 'Number'
  if (dtype === 'bool') return 'Boolean'
  if (dtype === 'category') return 'Category'
  if (dtype.startsWith('datetime')) return 'Date/Time'
  if (dtype === 'object' || dtype === 'str' || dtype === 'string') return 'Text'
  return dtype
}
