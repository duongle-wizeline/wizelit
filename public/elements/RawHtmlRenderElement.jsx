import React from 'react';

export default function RawHtmlRenderElement() {
  return (
    <div>
      <div dangerouslySetInnerHTML={{ __html: props.htmlString }} >
      </div>
    </div>
  );
};

