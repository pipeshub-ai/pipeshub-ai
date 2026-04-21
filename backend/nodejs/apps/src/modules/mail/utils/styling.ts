export const addStyling =( templateData:Record<string,any>) => {
  const containerstyle = `style="border: 0; border-spacing: 0; font-size: 13px; color: #333; border-collapse: collapse; overflow: hidden; margin: 0 auto; border-color: #b3b3b3; border: 3; width: 100%; background-color: #ffffff;"
        border="0"
        width="100%"
        cellspacing="0"
        cellpadding="0"
        align="center"
        valign="middle"`;
  const bodytablestyle = `  style="border: 0; border-spacing: 0; font-size: 13px; color: #333; border-collapse: collapse; overflow: hidden; margin: 0 auto; border-color: #b3b3b3; border: 3; width:560px ;
	max-width: 560px; background-color: #f5f5f5; padding: 15px;"
              class="mobile-width"
               
              cellspacing="0"
              cellpadding="0"
              align="center"
              valign="middle"`;

  const divstyle = ` style="max-width: 600px; margin: 0 5% 0 5% ;"`;
  const pstyle = `style="text-align: center;"`;
  const buttonstyle = `style="border: none; color: rgb(255, 255, 255); padding: 16px 32px; text-align: center; display: inline-block; font-size: 15px; margin: 4px 2px; cursor: pointer; background-color: #527798; width: 60%; font-weight: bold; border-radius: 5px;"`;

  const tabledata = `style="width: 50%; padding: 10px; text-align: left;"`;
  const tablestyle = `border="3" style="border-collapse: collapse; border-color: #b3b3b3; border: 3; width: 100%;" cellspacing="0" cellpadding="0"`;
  const stylingobject = {
    containerstyle: containerstyle,
    bodytablestyle: bodytablestyle,
    divstyle: divstyle,
    pstyle: pstyle,
    buttonstyle: buttonstyle,
    tabledata: tabledata,
    tablestyle: tablestyle
  };

  return { ...templateData, ...stylingobject };
};
