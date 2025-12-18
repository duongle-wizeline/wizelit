import React from 'react';

export default function RawHtmlRenderElement() {
  return (
    <div className='rawHtmlRenderElement'>
      <div dangerouslySetInnerHTML={{ __html: props.htmlString }} >
      </div>
    </div>
  );
};

